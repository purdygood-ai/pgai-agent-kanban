"""
process_progress.py
===================
Event-driven waits for tests that drive real processes (a private tmux
server, a dashboard script, a hook-launched sender), guarded against a
hang rather than bounded by a duration.

WHY THIS EXISTS
---------------
A test that waits on a fixed span of wall-clock time -- a settle sleep, a
polling deadline, a subprocess timeout -- couples its verdict to how busy
the host is: a loaded host outruns the span and the test fails while the
code under test is fine. The waits here end on the event itself (a
process exits, a file appears) and fail only when nothing observable has
changed for STALL_SECONDS in a row, so a slow-but-working step always
finishes and a genuinely hung one is still reported.

WHAT COUNTS AS PROGRESS
-----------------------
process_tree_activity() snapshots a set of root processes -- given pids,
plus every process whose command line contains a marker string such as a
private tmux socket name (the tmux server keeps the command line of the
client that forked it) -- together with all their descendants, as
(pid, kernel start time, CPU ticks) triples. A process starting, exiting,
or consuming CPU changes the snapshot.

SPINNING
--------
A process stuck in a loop that keeps burning CPU never stalls, so the
stall guard alone would let it run forever. run_until_exit() therefore
also accepts a CPU-time ceiling: the CPU the whole tree has consumed
(process_tree_cpu_seconds()) is the work done, which a loaded host does
not inflate the way it inflates elapsed time, so a ceiling set far above
what the command costs when it terminates fails only a command that is
not going to.

LIVENESS
--------
pane_process_death() reads a pane process's state directly from /proc, so
a process that was fatally signalled is reported dead before tmux gets to
reap it and drop or flag its pane.

Everything here reads /proc; require_procfs() narrates a skip (never a
silent one) on a host without it.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time
from typing import Callable, Iterable, Mapping, Sequence

import pytest

# How long a step may go with nothing observable changing before it is
# declared hung. It bounds a stall, not a duration -- a slow step that keeps
# consuming CPU, starting processes, or producing output never reaches it.
STALL_SECONDS = 60
POLL_SECONDS = 0.1

_PROC = pathlib.Path("/proc")


def require_procfs(label: str) -> None:
    """Narrate a skip naming <label> when /proc is not available."""
    if not (_PROC / "self" / "stat").exists():
        reason = f"{label}: /proc is not available on this host"
        print(f"SKIP: {reason}")
        pytest.skip(reason)


def proc_stat_fields(pid: int) -> list[str] | None:
    """Fields of /proc/<pid>/stat after the parenthesised command name
    (index 0 is the state letter, index 1 the parent pid), or None once
    the process is gone."""
    try:
        raw = (_PROC / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    return raw[raw.rindex(")") + 2 :].split()


def _cpu_ticks_from_fields(fields: Sequence[str]) -> int:
    # utime, stime, cutime, cstime are stat fields 14-17 (1-based), i.e.
    # indices 11-14 after the command name.
    return sum(int(v) for v in fields[11:15])


def proc_cpu_ticks(pid: int) -> int | None:
    """CPU time the process and its reaped children have used, in clock
    ticks -- the progress signal a slow-but-working process always moves."""
    fields = proc_stat_fields(pid)
    return None if fields is None else _cpu_ticks_from_fields(fields)


def proc_start_time(pid: int) -> str | None:
    """Kernel start time of <pid> (stat field 22); pairs with the pid so a
    recycled pid is never mistaken for the original process."""
    fields = proc_stat_fields(pid)
    return None if fields is None else fields[19]


def pane_process_death(pid: int, start_time: str) -> str | None:
    """None while the process <pid> (started at <start_time>) is alive and
    not on its way out; otherwise a short description of how it died. A
    fatally signalled process shows as exiting (state Z/X, or SIGKILL
    pending -- the kernel marks a whole thread group that way the moment a
    fatal signal lands) before tmux gets to reap it and drop the pane."""
    fields = proc_stat_fields(pid)
    if fields is None:
        return "process gone"
    if fields[19] != start_time:
        return "process gone (pid now belongs to another process)"
    if fields[0] in ("Z", "X", "x"):
        return f"process exited (state {fields[0]})"
    try:
        status = (_PROC / str(pid) / "status").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return "process gone"
    kill_bit = 1 << (int(signal.SIGKILL) - 1)
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key in ("SigPnd", "ShdPnd") and int(value.strip(), 16) & kill_bit:
            return "process is being killed (SIGKILL pending)"
    return None


def process_tree_activity(
    root_pids: Iterable[int] = (), cmdline_marker: str | None = None
) -> frozenset[tuple[int, str, int]]:
    """Snapshot of (pid, start time, CPU ticks) for every root process and
    all of its descendants. Roots are <root_pids> plus, when
    <cmdline_marker> is given, every process whose command line contains
    it. Two snapshots differ whenever any of those processes started,
    exited, or consumed CPU in between."""
    stats: dict[int, list[str]] = {}
    children: dict[int, list[int]] = {}
    roots = set(root_pids)
    marker = cmdline_marker.encode() if cmdline_marker else None
    for entry in _PROC.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        fields = proc_stat_fields(pid)
        if fields is None:
            continue
        stats[pid] = fields
        children.setdefault(int(fields[1]), []).append(pid)
        if marker is not None:
            try:
                if marker in (entry / "cmdline").read_bytes():
                    roots.add(pid)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue

    seen: set[int] = set()
    pending = [pid for pid in roots if pid in stats]
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pending.extend(children.get(pid, ()))
    return frozenset((pid, stats[pid][19], _cpu_ticks_from_fields(stats[pid])) for pid in seen)


def process_tree_cpu_seconds(activity: Iterable[tuple[int, str, int]]) -> float:
    """CPU seconds a process_tree_activity() snapshot accounts for: every
    live process's own CPU plus that of the children it has already
    reaped, so work done by a descendant that has since exited still
    counts."""
    return sum(ticks for _pid, _start, ticks in activity) / os.sysconf("SC_CLK_TCK")


def wait_while_progressing(
    done: Callable[[], bool],
    progress: Callable[[], object],
    *,
    what: str,
    on_stall: str | None = None,
) -> None:
    """Poll until done() is true. Fail only when progress() has returned
    the same value for STALL_SECONDS in a row. The failure says <what>
    made no progress and then <on_stall> when given -- the verdict a
    stalled wait means for the calling test -- or, by default, that the
    step is hung rather than slow."""
    last = progress()
    last_change = time.monotonic()
    while not done():
        time.sleep(POLL_SECONDS)
        current = progress()
        if current != last:
            last, last_change = current, time.monotonic()
        elif time.monotonic() - last_change >= STALL_SECONDS:
            detail = on_stall if on_stall is not None else "it is hung, not slow"
            pytest.fail(
                f"{what} made no observable progress for {STALL_SECONDS}s "
                f"(last observed state: {last!r}) -- {detail}"
            )


def run_until_exit(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cmdline_marker: str | None = None,
    own_process_group: bool = False,
    cpu_seconds_ceiling: float | None = None,
) -> subprocess.CompletedProcess:
    """Run <argv> to completion and return its CompletedProcess (text
    stdout/stderr), however long a loaded host takes. The guard is a
    stall: the process, its descendants, and every process whose command
    line contains <cmdline_marker> (the server a client is waiting on)
    showing no change for STALL_SECONDS. A stalled process is killed and
    the test fails naming the command as hung.

    <cpu_seconds_ceiling>, when given, adds a guard for a command that
    spins rather than stalls: once the CPU the tree has consumed passes
    it, the command is killed and the test fails naming it as spinning.
    Choose it far above what the command costs when it terminates -- it
    bounds work done, not elapsed time, so host load does not move it.

    <own_process_group> starts the command in its own session and kills
    the whole group when the command is abandoned, so a hung command's
    descendants die with it instead of outliving the test."""
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env) if env is not None else None,
        start_new_session=own_process_group,
    )
    last: object = None
    last_change = time.monotonic()
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass  # still running; the guards below decide whether to wait on
            # Checked outside the except block so a failure reports the
            # hang or spin verdict alone, not chained to the poll interval.
            current = process_tree_activity((proc.pid,), cmdline_marker)
            if cpu_seconds_ceiling is not None:
                used = process_tree_cpu_seconds(current)
                if used > cpu_seconds_ceiling:
                    pytest.fail(
                        f"{' '.join(argv)} consumed {used:.1f}s of CPU, past "
                        f"its {cpu_seconds_ceiling:g}s CPU ceiling, without "
                        "exiting -- it is spinning, not slow"
                    )
            if current != last:
                last, last_change = current, time.monotonic()
            elif time.monotonic() - last_change >= STALL_SECONDS:
                pytest.fail(
                    f"{' '.join(argv)} made no observable progress for "
                    f"{STALL_SECONDS}s -- it is hung, not slow"
                )
    finally:
        if proc.poll() is None:
            if own_process_group:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # the whole group exited after poll() looked
            else:
                proc.kill()
            proc.communicate()
    return subprocess.CompletedProcess(list(argv), proc.returncode, stdout, stderr)
