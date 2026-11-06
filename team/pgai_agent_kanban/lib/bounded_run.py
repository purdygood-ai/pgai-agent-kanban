"""
bounded_run.py -- run a child process in its own process group, under a time limit.

The child starts as the leader of a new process group, so everything it
starts that stays in the group -- a stuck ``git``, a background daemon a
script forked -- can be signalled together. When the limit passes, the whole
group is stopped: SIGTERM, then a grace period in which every member may
finish its SIGTERM cleanup and the output drains -- cut short once the group
is empty -- then SIGKILL to whatever is left. The caller always gets an
answer; a hung child never holds it.

A child that finishes inside the limit is left exactly as ``subprocess.run``
would leave it: nothing is signalled.

The limit itself comes from configuration. :func:`parse_timeout_seconds`
reads a configured value liberally -- absent or blank means the caller's
default -- and never answers "no limit": a value that is not a positive
whole number falls back to the default with a warning for the caller to
print, so a typo cannot unbound a child.

Usage (import)::

    from pgai_agent_kanban.lib.bounded_run import parse_timeout_seconds, run_in_process_group
    from pgai_agent_kanban.lib.hook_timeout import hook_timeout_default_seconds

    seconds, warning = parse_timeout_seconds(
        raw, hook_timeout_default_seconds(), setting="[release] hook_timeout_seconds", source=path
    )
    done = run_in_process_group(argv, timeout_seconds=seconds, capture_output=True, text=True)
    if done.timed_out:
        ...  # the group was stopped; done.stdout holds the output read so far

Usage (command line, for shell callers)::

    python3 -m pgai_agent_kanban.lib.bounded_run --timeout-seconds 900 -- COMMAND [ARG...]

The command inherits this process's working directory, environment and
standard streams, so its output reaches the caller as it is written. The
exit status is the command's own, 128+N when signal N ended it, 124 when
the limit passed and its group was stopped (as GNU ``timeout`` reports),
126 when it could not be started and 127 when it was not found. A SIGTERM
or SIGINT to this process stops the command's group before it exits.

Public API:
    KILL_GRACE_SECONDS
    TIMED_OUT_EXIT
    BoundedRun
    run_in_process_group(argv, *, timeout_seconds, kill_grace_seconds=..., **popen_kwargs) -> BoundedRun
    parse_timeout_seconds(raw, default, *, setting, source) -> tuple[int, Optional[str]]
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import dataclasses
import errno
import os
import re
import signal
import subprocess
import sys
import time
from typing import Any
from collections.abc import Sequence

__all__ = [
    "KILL_GRACE_SECONDS",
    "TIMED_OUT_EXIT",
    "BoundedRun",
    "main",
    "parse_timeout_seconds",
    "run_in_process_group",
]

#: Seconds a stopped group gets after SIGTERM before SIGKILL, and the most
#: spent reading what output remains after SIGKILL.
KILL_GRACE_SECONDS = 5.0

#: Seconds between checks, during the grace period, for a group that has emptied.
_GROUP_POLL_SECONDS = 0.05

#: Exit status of the command line when the limit passed and the group was
#: stopped -- the status GNU ``timeout`` uses, so shell callers test one value.
TIMED_OUT_EXIT = 124

_WHOLE_NUMBER_RE = re.compile(r"^(\d+)$")


@dataclasses.dataclass(frozen=True)
class BoundedRun:
    """What one :func:`run_in_process_group` call returned.

    Attributes:
        returncode:      The child's exit status, or None when it timed out.
        stdout:          Captured standard output (``str`` or ``bytes``, as
                         the Popen arguments asked), or None when not
                         captured. After a timeout: what was read before the
                         group stopped, or None when nothing could be read.
        stderr:          The same for standard error.
        timed_out:       True when the limit passed and the group was stopped.
        timeout_seconds: The limit the child ran under.
    """

    returncode: int | None
    stdout: Any
    stderr: Any
    timed_out: bool
    timeout_seconds: float


def run_in_process_group(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    kill_grace_seconds: float = KILL_GRACE_SECONDS,
    **popen_kwargs: Any,
) -> BoundedRun:
    """Run *argv* as the leader of a new process group, stopping the group after *timeout_seconds*.

    *popen_kwargs* go to :class:`subprocess.Popen` unchanged (``cwd``,
    ``env``, ``stdin``, ``stdout``/``stderr`` or ``capture_output``,
    ``text``, ``errors``, ``pass_fds``...); ``process_group`` is always 0.

    Raises:
        OSError: the child could not be started (missing file, ENOEXEC...),
            exactly as :func:`subprocess.run` raises it.
    """
    if popen_kwargs.pop("capture_output", False):
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(list(argv), process_group=0, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_group(proc, kill_grace_seconds)
        return BoundedRun(None, stdout, stderr, True, timeout_seconds)
    except BaseException:
        # An interrupt in the caller must not leave the group running.
        _stop_group(proc, kill_grace_seconds)
        raise
    return BoundedRun(proc.returncode, stdout, stderr, False, timeout_seconds)


def _signal_group(pgid: int, sig: int) -> None:
    """Send *sig* to process group *pgid*; an empty group is not an error."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _group_has_members(pgid: int) -> bool:
    """True while process group *pgid* has a member; one owned by another user counts."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_group_exit(proc: subprocess.Popen[Any], deadline: float) -> bool:
    """Wait until *proc*'s process group is empty or *deadline* passes; True when it emptied.

    *deadline* is a :func:`time.monotonic` value. The leader is reaped as
    soon as it exits, so its zombie is not counted as a member.
    """
    while True:
        proc.poll()
        if not _group_has_members(proc.pid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_GROUP_POLL_SECONDS, remaining))


def _stop_group(proc: subprocess.Popen[Any], grace: float) -> tuple[Any, Any]:
    """Stop *proc*'s whole process group and reap *proc*; return the output read.

    SIGTERM goes to the group, which then gets *grace* seconds: the output
    drains, and every member -- the leader and anything it started that is
    still in the group -- may finish its SIGTERM cleanup, even after the
    leader has exited. The wait ends early once the group is empty. SIGKILL
    then goes to whatever is left of the group, whether or not the leader
    has exited, so a member that ignored SIGTERM -- one that does not hold
    the output pipes included -- does not outlive the call. A descendant
    that left the group and still holds the pipes cannot hold the call
    either: reading stops after one more grace period.
    """
    deadline = time.monotonic() + grace
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        output = proc.communicate(timeout=grace)
        drained = True
    except subprocess.TimeoutExpired:
        output, drained = (None, None), False
    if not _wait_for_group_exit(proc, deadline):
        _signal_group(proc.pid, signal.SIGKILL)
    if not drained:
        try:
            output = proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            proc.wait()
    return output


def parse_timeout_seconds(raw: str | None, default: int, *, setting: str, source: str) -> tuple[int, str | None]:
    """Read a configured timeout: a positive whole number of seconds.

    Args:
        raw:     The value as read from the configuration file, or None when
                 the key is absent.
        default: Seconds used when *raw* is absent, blank or unusable.
        setting: The key as an operator would look for it, for the warning
                 (``[release] hook_timeout_seconds``).
        source:  The file *raw* came from, for the warning.

    Returns:
        ``(seconds, warning)``. *warning* is None for a usable value or an
        absent/blank one; for anything else (``0``, a sign, a unit, a word)
        it is one line saying the default is used, for the caller to print.
        Zero is not "no limit": every child stays bounded.
    """
    text = (raw or "").strip()
    if not text:
        return default, None
    match = _WHOLE_NUMBER_RE.match(text)
    if match and int(match.group(1)) > 0:
        return int(match.group(1)), None
    return default, (
        f"WARNING: {setting} = {text!r} in {source} is not a positive whole number of seconds; "
        f"using the default, {default}s."
    )


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.bounded_run",
        description=(
            "Run COMMAND as the leader of a new process group. When the limit passes, the whole "
            "group is stopped -- SIGTERM, then SIGKILL after the grace period -- including any "
            "background process COMMAND started that ignores SIGTERM. COMMAND inherits the working "
            "directory, environment and standard streams."
        ),
        epilog=(
            "Exit status: COMMAND's own; 128+N when signal N ended it; 124 when the limit passed "
            "and its group was stopped; 126 when it could not be started; 127 when it was not found. "
            "Example: python3 -m pgai_agent_kanban.lib.bounded_run --timeout-seconds 900 -- sleep 5"
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        required=True,
        metavar="SECONDS",
        help="the limit COMMAND runs under; must be greater than zero",
    )
    parser.add_argument(
        "--kill-grace-seconds",
        type=float,
        default=KILL_GRACE_SECONDS,
        metavar="SECONDS",
        help=f"seconds between SIGTERM and SIGKILL once the limit passes (default {KILL_GRACE_SECONDS:g})",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="-- COMMAND [ARG...]",
        help="the command to run and its arguments, after --; required",
    )
    return parser


def _stop_on_signal(signum: int, _frame: Any) -> None:
    """Turn SIGTERM into an exit that stops the command's group on the way out."""
    signal.signal(signum, signal.SIG_IGN)  # a second signal must not cut the stop short
    raise SystemExit(128 + signum)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry: run one command under a limit; return the exit status to use."""
    parser = _cli_parser()
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no COMMAND given; example: --timeout-seconds 900 -- sleep 5")
    if args.timeout_seconds <= 0 or args.kill_grace_seconds < 0:
        parser.error("--timeout-seconds must be greater than zero and --kill-grace-seconds not negative")
    signal.signal(signal.SIGTERM, _stop_on_signal)
    try:
        done = run_in_process_group(
            command, timeout_seconds=args.timeout_seconds, kill_grace_seconds=args.kill_grace_seconds
        )
    except KeyboardInterrupt:
        return 128 + signal.SIGINT
    except OSError as exc:
        print(f"bounded_run: cannot run {command[0]}: {exc.strerror}", file=sys.stderr)
        return 127 if exc.errno == errno.ENOENT else 126
    if done.timed_out:
        return TIMED_OUT_EXIT
    code = done.returncode
    return 128 - code if code is not None and code < 0 else int(code or 0)


if __name__ == "__main__":
    sys.exit(main())
