"""
test_dashboard_activation_hook_lifecycle.py
=============================================
End-to-end integration coverage for the dashboard's session-scoped
activation-hook lifecycle: team/scripts/dashboard/create.sh (install),
team/scripts/dashboard/kill.sh (removal), and
team/scripts/dashboard/activation-hook-sender.sh (the sender the hook
fires against on every window switch).

Acceptance scenario exercised: create a session on a
fresh private tmux server (tmux -L <private>, no pre-existing session),
then immediately -- no warm-up sleep, which would hide the defect --
perform 30 window switches. The regression this guards against is
invisible until an operator hits it: a hook that fires against a pane
whose signal trap has not been proven armed kills that pane (and, for
window "main"'s pane 0, the entire tmux server) outright.

Coverage:
  - create.sh returns cleanly against the real ~14-window board (no
    all-armed-handshake refusal), and the installed hook is present.
  - 30 immediate window switches leave every pane id from before the
    storm still present and reporting pane_dead=0 -- compared by pane id,
    not by count alone, so a pane that died and was silently replaced by
    a same-count-but-different-id pane would still fail this check.
  - kill.sh removes the session-scoped hook and the session itself;
    running kill.sh a second time still exits 0 (idempotent).
  - Firing several activation-hook-sender.sh invocations at once, truly
    overlapping (never awaited one at a time), against the identical
    window leaves every one of its panes alive -- the per-pane work
    idempotence acceptance clause, proven directly rather than asserted
    in prose.

Every wait in this module ends on the event it is waiting for -- the
process exiting, the last sender finishing -- never on a fixed span of
wall-clock time. Each wait still carries a hang guard, but the guard
measures how long nothing observable has changed (no CPU consumed, no new
pane, no new armed marker, no new output), so a loaded host that is slow
but working never trips it; only a genuinely hung step does, and its
failure says so rather than reading as a pane death. Pane survival is
checked twice: by tmux's own pane list, and directly against each pane's
process, so a pane killed a moment before the check is caught even when
tmux has not reaped it yet.

Docker is not used anywhere in this module -- every tmux operation runs on
a private socket on the test host directly (tmux -L <private>), matching
the convention team/tests/unit/test_dashboard_pane_signal_traps.py and
team/tests/unit/test_dashboard_activation_hook_lifecycle.py already
establish. Skipped (narrated, never silently) when tmux is not on PATH.

Test names describe behaviour under test, never bug/task IDs
(team/roles/TESTER.md, Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import uuid
from typing import Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import (
    narrate_binary_skip,
    teardown_private_tmux,
    try_resolve_binary,
)
from tests.fixtures.process_progress import (
    pane_process_death,
    proc_cpu_ticks,
    proc_start_time,
    require_procfs,
    wait_while_progressing,
)

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location.
# test file:  team/tests/integration/test_dashboard_activation_hook_lifecycle.py
# team/:      .../team
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"

_SWITCH_COUNT = 30
_TMUX_SKIP_LABEL = "activation-hook-lifecycle"
_SENDER_BASENAME = "activation-hook-sender.sh"

# What a stalled wait means for these tests: a hung step, never a verdict
# on pane survival.
_STALL_VERDICT = "it is hung, not slow; this is not a verdict on pane survival"

# create.sh's all-armed handshake refuses once PGAI_DASHBOARD_ARM_TIMEOUT_SECONDS
# pass with no further pane arming (operator default 20). Forty panes on a
# loaded host can take longer than that to arm their first one, which would
# make host load the verdict of tests about pane survival. These tests set
# the bound far above any contended run, so the handshake ends on every pane
# arming; the bound is left only as a backstop for a pane that never arms.
_ARM_STALL_BACKSTOP_SECONDS = 600


class PrivateTmux(NamedTuple):
    """A private, isolated tmux server plus the env every subprocess this
    test spawns (including create.sh/kill.sh, which invoke the bare `tmux`
    word with no socket flag of their own) must inherit to land on it."""

    real_tmux: str
    socket_name: str
    env: dict[str, str]

    def run(self, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.real_tmux, "-L", self.socket_name, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


@pytest.fixture
def private_tmux(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    """Isolates every tmux invocation onto a private-socket tmux server
    (tmux -L <private>) with no pre-existing session, so no test touches
    a tmux server it did not start. create.sh and kill.sh always invoke
    the bare `tmux` word, so isolation is achieved with a PATH-prepended
    wrapper resolving `tmux` to `<real-tmux-absolute-path> -L <private>`."""
    # Resolve without skipping yet: the skip decision below happens INSIDE
    # the try block so that, on a scrubbed PATH, this fixture's own finally
    # clause genuinely runs guard.teardown() as a real no-op (proving the
    # teardown path exercised, not merely reasoned about) instead of the
    # skip unwinding before the try is ever entered.
    guard = try_resolve_binary("tmux")
    socket_name = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label=_TMUX_SKIP_LABEL)
        real_tmux = guard.path

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "tmux"
        wrapper.write_text(
            f"#!/usr/bin/env bash\nexec {shlex.quote(real_tmux)} -L {shlex.quote(socket_name)} \"$@\"\n"
        )
        wrapper.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

        pt = PrivateTmux(real_tmux=real_tmux, socket_name=socket_name, env=env)
        yield pt
    finally:
        teardown_private_tmux(guard, socket_name)


def _count_panes(private_tmux: PrivateTmux) -> int | None:
    """Pane count across the private server, or None when the server is
    not up yet or the query did not answer -- a progress probe only."""
    try:
        listed = private_tmux.run("list-panes", "-a", "-F", "#{pane_id}")
    except subprocess.TimeoutExpired:
        return None
    return len(listed.stdout.split()) if listed.returncode == 0 else None


def _run_create_sh(
    private_tmux: PrivateTmux, env: dict[str, str], session: str, work_dir: pathlib.Path
) -> str:
    """Run create.sh to completion and return its combined output. Waits
    for the process itself to exit -- however long a loaded host takes to
    build every window and arm every pane -- guarded only against a stall:
    no new pane, no new armed marker, no new output and no CPU consumed by
    create.sh or the tmux commands it runs, for the shared stall bound
    (tests/fixtures/process_progress.py STALL_SECONDS)."""
    log_path = work_dir / f"create-{session}.log"
    armed_dir = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "dashboard" / "armed" / session
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            ["bash", str(_CREATE_SH), "--kanban-root", str(_REPO_ROOT), "--session", session],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        try:
            wait_while_progressing(
                lambda: proc.poll() is not None,
                lambda: (
                    proc_cpu_ticks(proc.pid),
                    _count_panes(private_tmux),
                    len(list(armed_dir.iterdir())) if armed_dir.is_dir() else 0,
                    log_path.stat().st_size,
                ),
                what="create.sh",
                on_stall=_STALL_VERDICT,
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    return log_path.read_text()


def _dashboard_env(private_tmux: PrivateTmux, kanban_temp: pathlib.Path) -> dict[str, str]:
    """The env create.sh, the senders and kill.sh run under: the private tmux
    server, the repo as kanban root, a scratch temp dir, and the test-scoped
    arm stall bound (_ARM_STALL_BACKSTOP_SECONDS)."""
    env = dict(private_tmux.env)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_REPO_ROOT)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(kanban_temp)
    env["TERM"] = "xterm-256color"
    env["PGAI_DASHBOARD_ARM_TIMEOUT_SECONDS"] = str(_ARM_STALL_BACKSTOP_SECONDS)
    return env


def _assert_all_panes_armed(create_output: str) -> None:
    """create.sh got past its all-armed handshake. With the test-scoped
    bound, a refusal means no pane armed for _ARM_STALL_BACKSTOP_SECONDS."""
    assert "dashboard_create.pane_arm_timeout" not in create_output, (
        f"create.sh's all-armed handshake saw no pane arm for "
        f"{_ARM_STALL_BACKSTOP_SECONDS}s -- {_STALL_VERDICT}:\n{create_output}"
    )


def _pane_processes(private_tmux: PrivateTmux, target: str, *, all_windows: bool) -> dict[str, tuple[int, str]]:
    """{pane_id: (pane_pid, kernel start time)} for the panes under <target>."""
    scope = ["-a", "-t", target] if all_windows else ["-t", target]
    listed = private_tmux.run("list-panes", *scope, "-F", "#{pane_id} #{pane_pid}")
    panes: dict[str, tuple[int, str]] = {}
    for line in listed.stdout.splitlines():
        pane_id, pid_text = line.split()
        start_time = proc_start_time(int(pid_text))
        assert start_time is not None, f"pane {pane_id}'s process {pid_text} vanished before the test began"
        panes[pane_id] = (int(pid_text), start_time)
    return panes


def _assert_pane_processes_alive(panes: dict[str, tuple[int, str]], after_what: str) -> None:
    deaths = {
        pane_id: death
        for pane_id, (pid, start_time) in panes.items()
        if (death := pane_process_death(pid, start_time)) is not None
    }
    assert not deaths, (
        f"zero pane deaths is the assertion; after {after_what} these panes' "
        f"processes died (checked directly, ahead of tmux reaping them): {deaths}"
    )


def _session_sender_pids(session: str) -> list[int]:
    """Pids of every activation-hook-sender.sh process (and the `sh -c`
    tmux's run-shell wraps it in) still running against <session>."""
    pids = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if _SENDER_BASENAME in cmdline and session in cmdline:
            pids.append(int(entry.name))
    return sorted(pids)


def _wait_for_session_senders_to_finish(session: str) -> None:
    """Block until no hook-launched sender for <session> is still running.
    The session-window-changed hook backgrounds each sender (run-shell -b),
    so select-window returns while they are still in flight; reading pane
    state before they finish would pass without their signals having been
    sent at all."""
    def snapshot() -> tuple:
        return tuple((pid, proc_cpu_ticks(pid)) for pid in _session_sender_pids(session))

    wait_while_progressing(
        lambda: not _session_sender_pids(session),
        snapshot,
        what=f"the activation-hook senders launched against session {session}",

        on_stall=_STALL_VERDICT,
    )


def test_thirty_immediate_window_switches_leave_every_pane_alive(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    require_procfs(_TMUX_SKIP_LABEL)
    session = f"storm-{uuid.uuid4().hex[:6]}"
    kanban_temp = tmp_path / "kanban-temp"
    kanban_temp.mkdir()

    env = _dashboard_env(private_tmux, kanban_temp)

    create_output = _run_create_sh(private_tmux, env, session, tmp_path)
    # A no-tty "open terminal failed" on the final exec-attach line is
    # expected and harmless headless -- the session was already created
    # detached, with the all-armed handshake and hook install already
    # complete, before that final line runs.
    _assert_all_panes_armed(create_output)

    try:
        assert private_tmux.run("has-session", "-t", session).returncode == 0, (
            f"expected the dashboard session to exist after create.sh ran; "
            f"create.sh output:\n{create_output}"
        )

        # The hook must be installed and session-scoped by the time
        # create.sh has returned -- the ordering acceptance criterion,
        # proven functionally here (the unit-level module proves it by
        # source order).
        hooks = private_tmux.run("show-hooks", "-t", session)
        assert "session-window-changed" in hooks.stdout, (
            f"expected the activation hook to be installed on the session "
            f"create.sh just returned from; show-hooks output: {hooks.stdout!r}"
        )

        windows = private_tmux.run(
            "list-windows", "-t", session, "-F", "#{window_name}"
        ).stdout.split()
        assert len(windows) >= 2, f"expected multiple windows to switch across; got: {windows}"

        before_processes = _pane_processes(private_tmux, session, all_windows=True)
        before = sorted(before_processes)
        assert before, "expected at least one pane before the switch storm"

        # The storm itself: 30 IMMEDIATE window switches, no warm-up sleep
        # anywhere in this loop -- a sleep here would hide exactly the
        # race this fixture exists to catch.
        for i in range(_SWITCH_COUNT):
            target = windows[i % len(windows)]
            select = private_tmux.run("select-window", "-t", f"{session}:{target}")
            assert select.returncode == 0, (
                f"select-window failed on switch {i + 1}/{_SWITCH_COUNT} "
                f"(target {target}): {select.stderr}"
            )

        # Every switch backgrounded a sender; the verdict is about what
        # those senders do, so it is read only once the last one is done.
        _wait_for_session_senders_to_finish(session)
        _assert_pane_processes_alive(before_processes, f"{_SWITCH_COUNT} immediate window switches")

        after = sorted(
            private_tmux.run("list-panes", "-a", "-t", session, "-F", "#{pane_id}").stdout.split()
        )
        assert after == before, (
            "zero pane deaths is the assertion -- compared by pane id, not "
            f"count alone.\nBefore ({len(before)}): {before}\n"
            f"After  ({len(after)}): {after}\n"
            f"Missing: {sorted(set(before) - set(after))}\n"
            f"New/unexpected: {sorted(set(after) - set(before))}"
        )

        dead_flags = private_tmux.run(
            "list-panes", "-a", "-t", session, "-F", "#{pane_dead}"
        ).stdout.split()
        assert dead_flags and all(flag == "0" for flag in dead_flags), (
            f"expected every pane to still be alive and rendering "
            f"(pane_dead=0) after the storm; got: {dead_flags}"
        )
    finally:
        kill_result = subprocess.run(
            ["bash", str(_KILL_SH), "--kanban-root", str(_REPO_ROOT), "--session", session],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert kill_result.returncode == 0, f"kill.sh failed: {kill_result.stdout}\n{kill_result.stderr}"

    # Session (and, with it, its session-scoped hook) is fully gone.
    assert private_tmux.run("has-session", "-t", session).returncode != 0

    # kill.sh stays idempotent on the already-gone session.
    kill_again = subprocess.run(
        ["bash", str(_KILL_SH), "--kanban-root", str(_REPO_ROOT), "--session", session],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert kill_again.returncode == 0, f"kill.sh (second run) failed: {kill_again.stdout}\n{kill_again.stderr}"


_SENDER_SH = _TEAM_DIR / "scripts" / "dashboard" / "activation-hook-sender.sh"
_OVERLAP_SENDER_COUNT = 8


def test_signaling_the_same_pane_twice_in_overlap_leaves_it_alive(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """
    Idempotence of the per-pane hook work, proven directly rather than
    asserted in prose: invoking the activation hook's per-pane work --
    team/scripts/dashboard/activation-hook-sender.sh, the exact script
    the session-window-changed hook backgrounds via `run-shell -b` on
    every switch -- more than once, OVERLAPPING (real concurrent
    processes, none awaited before the next starts), against the SAME
    window's panes must leave every one of them alive. A rapid switch
    storm launches one sender per switch with no guarantee the previous
    one has finished, so an activation superseded by another must never
    take a pane down with it.

    Targets window "main" explicitly (via --window) rather than relying
    on "whatever window is currently selected" -- every overlapping
    sender invocation below is guaranteed to race over the identical
    pane set this way, which is the property under test.
    """
    require_procfs(_TMUX_SKIP_LABEL)
    session = f"overlap-{uuid.uuid4().hex[:6]}"
    kanban_temp = tmp_path / "kanban-temp"
    kanban_temp.mkdir()

    env = _dashboard_env(private_tmux, kanban_temp)

    create_output = _run_create_sh(private_tmux, env, session, tmp_path)
    _assert_all_panes_armed(create_output)

    try:
        assert private_tmux.run("has-session", "-t", session).returncode == 0, (
            f"expected the dashboard session to exist after create.sh ran; "
            f"create.sh output:\n{create_output}"
        )

        before_processes = _pane_processes(private_tmux, f"{session}:main", all_windows=False)
        before = sorted(before_processes)
        assert before, "expected at least one pane in window main before overlap"

        # Real overlap: launch every sender invocation via Popen (never
        # awaited individually before the next starts), all targeting the
        # identical window -- the shape a rapid, unsettled switch storm
        # produces when hook firings outpace any single sender's runtime.
        procs = [
            subprocess.Popen(
                [
                    "bash",
                    str(_SENDER_SH),
                    "--kanban-root",
                    str(_REPO_ROOT),
                    "--session",
                    session,
                    "--window",
                    "main",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(_OVERLAP_SENDER_COUNT)
        ]
        try:
            wait_while_progressing(
                lambda: all(proc.poll() is not None for proc in procs),
                lambda: tuple((proc.returncode, proc_cpu_ticks(proc.pid)) for proc in procs),
                what=f"{_OVERLAP_SENDER_COUNT} overlapping activation-hook-sender.sh invocations",
                on_stall=_STALL_VERDICT,
            )
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        for proc in procs:
            assert proc.returncode == 0, (
                f"activation-hook-sender.sh exited {proc.returncode} under overlap"
            )

        _assert_pane_processes_alive(
            before_processes, f"{_OVERLAP_SENDER_COUNT} overlapping sender invocations"
        )

        after = sorted(
            private_tmux.run(
                "list-panes", "-t", f"{session}:main", "-F", "#{pane_id}"
            ).stdout.split()
        )
        assert after == before, (
            f"{_OVERLAP_SENDER_COUNT} overlapping sender invocations against "
            "the same window killed or replaced a pane -- zero pane deaths "
            "is the assertion, compared by pane id, not count alone.\n"
            f"Before ({len(before)}): {before}\nAfter  ({len(after)}): {after}\n"
            f"Missing: {sorted(set(before) - set(after))}\n"
            f"New/unexpected: {sorted(set(after) - set(before))}"
        )

        dead_flags = private_tmux.run(
            "list-panes", "-t", f"{session}:main", "-F", "#{pane_dead}"
        ).stdout.split()
        assert dead_flags and all(flag == "0" for flag in dead_flags), (
            f"expected every window-main pane to still be alive after "
            f"{_OVERLAP_SENDER_COUNT} overlapping sender invocations; got: {dead_flags}"
        )
    finally:
        kill_result = subprocess.run(
            ["bash", str(_KILL_SH), "--kanban-root", str(_REPO_ROOT), "--session", session],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert kill_result.returncode == 0, f"kill.sh failed: {kill_result.stdout}\n{kill_result.stderr}"
