"""
tmux_sockets.py -- private tmux socket files left behind by test runs.

A test that starts a private tmux server (``tmux -L <name>``) gets a socket
file at ``$TMUX_TMPDIR/tmux-<uid>/<name>`` (``/tmp`` when ``TMUX_TMPDIR`` is
unset or empty). Stopping the server does not remove that file, so every
run would leave one behind. This module is the one place that knows where
those files live and how to tell a stale one from a live one:

  socket_path(name, env)      where ``tmux -L <name>`` puts its socket
  server_is_live(path)        whether a server still answers on <path>
  sweep_stale_sockets(...)    remove stale ``pgai-*`` sockets, keep the rest

The test fixtures' shared teardown (tests/fixtures/binary_guard.py,
teardown_private_tmux) removes the socket of the server it stopped; the
gated runners' post-run sweep (gated_runner_tmux_socket_sweep in
team/scripts/lib/gated_runner_lint.sh) runs this module's CLI to catch
whatever a killed run still left.

A socket is stale only when connecting to it is refused: nothing is
listening. Any other outcome -- a server accepts, a permission error, a
timeout -- counts as live, so the sweep never removes a socket it cannot
prove dead. The sweep never signals or kills a server. It also leaves alone
sockets younger than a minimum age (default 60 s), so a server that has
bound its socket but is not yet listening is not mistaken for a dead one.

CLI (always exits 0; one narration line on stdout):

  python3 -m pgai_agent_kanban.testing.tmux_sockets sweep \\
      [--dir DIR] [--prefix pgai-] [--min-age-seconds 60]

``--dir`` defaults to the current user's tmux directory under
``$TMUX_TMPDIR``.
"""

from __future__ import annotations

import argparse
import errno
import os
import pathlib
import socket
import stat
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_PREFIX = "pgai-"
DEFAULT_MIN_AGE_SECONDS = 60.0
_PROBE_TIMEOUT_SECONDS = 1.0


def tmux_socket_dir(env: Optional[Mapping[str, str]] = None) -> pathlib.Path:
    """The current user's tmux socket directory as tmux resolves it:
    ``$TMUX_TMPDIR/tmux-<uid>``, with ``/tmp`` for an unset or empty
    ``TMUX_TMPDIR``. *env* defaults to this process's environment."""
    source = os.environ if env is None else env
    base = source.get("TMUX_TMPDIR") or "/tmp"
    return pathlib.Path(base) / f"tmux-{os.getuid()}"


def socket_path(name: str, env: Optional[Mapping[str, str]] = None) -> pathlib.Path:
    """Where ``tmux -L <name>`` started under *env* puts its socket file."""
    return tmux_socket_dir(env) / name


def server_is_live(path: os.PathLike | str) -> bool:
    """True unless a connection to the unix socket at *path* is refused.

    A refused connection means no server is listening there. Every other
    outcome (accepted, timed out, permission denied, path too long) is
    treated as live, so a caller deciding whether to remove the file errs
    towards keeping it.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(_PROBE_TIMEOUT_SECONDS)
    try:
        probe.connect(os.fspath(path))
    except OSError as exc:
        return exc.errno != errno.ECONNREFUSED
    finally:
        probe.close()
    return True


@dataclass
class SweepResult:
    """What one sweep found in *directory*."""

    directory: pathlib.Path
    removed: list[pathlib.Path] = field(default_factory=list)
    live: list[pathlib.Path] = field(default_factory=list)
    too_young: list[pathlib.Path] = field(default_factory=list)


def sweep_stale_sockets(
    directory: Optional[os.PathLike | str] = None,
    *,
    prefix: str = DEFAULT_PREFIX,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    now: Optional[float] = None,
) -> SweepResult:
    """Remove every socket in *directory* whose name starts with *prefix*,
    that this user owns, that is at least *min_age_seconds* old and that has
    no live server. Everything else is left in place.

    *directory* defaults to tmux_socket_dir(). A missing directory is an
    empty sweep, never an error.
    """
    target = pathlib.Path(directory) if directory is not None else tmux_socket_dir()
    result = SweepResult(directory=target)
    try:
        entries = sorted(target.iterdir())
    except OSError:
        return result
    clock = time.time() if now is None else now
    uid = os.getuid()
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        try:
            info = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            continue
        if clock - info.st_mtime < min_age_seconds:
            result.too_young.append(entry)
            continue
        if server_is_live(entry):
            result.live.append(entry)
            continue
        try:
            entry.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            result.live.append(entry)
            continue
        result.removed.append(entry)
    return result


def _narrate(result: SweepResult, prefix: str) -> str:
    if result.removed:
        return (
            f"tmux_socket_sweep: removed {len(result.removed)} stale {prefix}* "
            f"socket(s) from {result.directory}; kept {len(result.live)} with a live server"
        )
    return f"tmux_socket_sweep: OK (no stale {prefix}* sockets in {result.directory})"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Command-line entry point: parse the ``sweep`` subcommand from *argv*,
    run sweep_stale_sockets() with its --dir, --prefix and --min-age-seconds,
    and print one narration line saying what was removed or kept.

    Always returns 0, so a cleanup step never fails its caller: a sweep
    that raises prints one "skipped" line naming the error instead.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.testing.tmux_sockets",
        description="Remove stale private tmux sockets left by test runs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sweep = sub.add_parser("sweep", help="remove stale sockets; always exits 0")
    sweep.add_argument("--dir", default=None, help="socket directory (default: the user's tmux dir under $TMUX_TMPDIR)")
    sweep.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"socket name prefix (default: {DEFAULT_PREFIX})")
    sweep.add_argument(
        "--min-age-seconds",
        type=float,
        default=DEFAULT_MIN_AGE_SECONDS,
        help=f"skip sockets younger than this (default: {DEFAULT_MIN_AGE_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    try:
        result = sweep_stale_sockets(
            args.dir, prefix=args.prefix, min_age_seconds=args.min_age_seconds
        )
    except Exception as exc:  # noqa: BLE001 - a cleanup step must never fail its caller
        print(f"tmux_socket_sweep: skipped ({exc})")
        return 0
    print(_narrate(result, args.prefix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
