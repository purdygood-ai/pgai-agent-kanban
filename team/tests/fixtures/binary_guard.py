"""
binary_guard.py
================
Shared availability guard for external binaries a test fixture depends on.

WHY THIS EXISTS
---------------
Every dashboard test module that drives a private tmux server wrote its own
copy of the same three-part contract: a probe for whether the binary is on
PATH, a skip narration that prints the reason before calling pytest.skip(),
and a resolver that ties the two together. Four copies is four chances to
diverge, and a fixture's teardown block is exactly where divergence turns
into a defect: a teardown written to assume setup always resolved the
binary reaches for that binary again even when setup never found it, and
raises where a skip was already owed.

THE CONTRACT
------------
1. Present means exercise: resolve_binary()/try_resolve_binary() return the
   absolute, symlink-resolved path when the binary is on PATH.
2. Absent means a narrated skip: require_binary() prints a reason naming the
   missing binary to stdout (so the skip is visible in a run log, not a
   blank space) and then calls pytest.skip() with that same reason. It never
   raises a plain exception for a missing binary and it never passes
   silently.
3. Neither present nor absent is ever treated as an error by a teardown
   step: BinaryGuard.teardown() only invokes the caller's teardown callable
   when setup actually resolved a path, so a fixture's finally block is safe
   to run unconditionally -- including when setup skipped before there was
   anything to tear down. It does not swallow any OTHER exception the
   teardown callable raises; a genuine teardown fault (a hung kill command,
   a permissions error) still propagates instead of being hidden behind a
   blanket `except Exception: pass`.

USAGE
-----
The common case -- resolve or skip, matching what every tmux fixture used
to do with its own private resolve-or-skip helper:

    from tests.fixtures.binary_guard import require_binary

    def some_fixture():
        guard = require_binary("tmux", label="pane-signal-traps")
        real_tmux = guard.path  # guaranteed non-None; require_binary()
                                 # already skipped otherwise

A fixture whose teardown must stay safe even though the binary may never
have been resolved (setup skipped, so no server was ever started to tear
down):

    def some_fixture():
        guard = require_binary("tmux", label="pane-signal-traps")
        ...
        try:
            yield something
        finally:
            guard.teardown(
                lambda path: subprocess.run(
                    [path, "-L", socket_name, "kill-server"],
                    capture_output=True,
                    timeout=10,
                )
            )

`guard.teardown()` is a no-op when `guard.resolved` is False, which is what
makes the finally block safe to write unconditionally. Because
`require_binary()` already skipped on an unresolved binary before the
fixture ever reaches its `try`, a guard produced by `require_binary()` is
always resolved by the time `.teardown()` can be called on it -- the same
`.teardown()` method also accepts a `try_resolve_binary()` guard directly,
for a fixture that wants to defer the skip decision past the point where
setup would otherwise need to raise.

PRIVATE TMUX SERVERS
--------------------
A fixture that starts a private tmux server (``tmux -L <name>``) tears it
down with teardown_private_tmux(), which stops the server and removes its
socket file, whatever the test's outcome -- a stopped server leaves the
file behind otherwise:

    def some_fixture():
        guard = try_resolve_binary("tmux")
        socket_name = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            ...
            yield something
        finally:
            teardown_private_tmux(guard, socket_name, env=env)

DOCKER'S SECOND FAILURE MODE: AN UNREACHABLE DAEMON
----------------------------------------------------
A container-engine binary being on PATH does not mean the engine is usable
-- the daemon behind it (the dockerd socket, a rootless podman service) can
be stopped, unreachable, or pointed at by a DOCKER_HOST that answers
nothing. Those are two different facts a test that needs docker has to
tell apart: "install docker" is not the remedy for "start the docker
daemon," and blurring the two into one skip reason destroys the signal an
operator needs to act on it. require_docker_daemon() extends the same
resolve-or-skip contract with a second check, run only after the binary
itself resolved:

    from tests.fixtures.binary_guard import require_docker_daemon

    def some_docker_fixture():
        guard = require_docker_daemon(label="docker-build-fixture")
        # guard.path is guaranteed non-None and the daemon answered --
        # require_docker_daemon() already skipped otherwise, naming
        # whichever of the two facts was false.

require_docker_daemon() never invents a rival availability contract: it
calls require_binary() for the absent-binary case (identical narration to
every other guarded binary) and only adds its own narration for the
distinct present-binary-unreachable-daemon case.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, NoReturn, Optional

import pytest


def binary_available(name: str) -> bool:
    """Return whether *name* resolves to an executable on PATH."""
    return shutil.which(name) is not None


def narrate_binary_skip(name: str, label: str) -> NoReturn:
    """Print a narrated skip line for *name* and raise pytest.skip().

    `label` tags the printed line the way each tmux module used to tag its
    own skip narration (e.g. "pane-signal-traps"), producing:

        [<label>] SKIP — <name> binary not found on PATH; this test
        requires <name>

    Never returns -- pytest.skip() always raises to unwind the test.
    """
    reason = f"{name} binary not found on PATH; this test requires {name}"
    print(f"\n[{label}] SKIP — {reason}", flush=True)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip() always raises")  # pragma: no cover


@dataclass(frozen=True)
class BinaryGuard:
    """The outcome of resolving (or failing to resolve) an external binary.

    Construct via try_resolve_binary() or require_binary() -- never build
    one by hand, since `path` must agree with what shutil.which() actually
    found for `name` at resolution time.

    Attributes:
        name: the binary name that was requested (e.g. "tmux").
        path: absolute, symlink-resolved path when resolution succeeded, or
              None when the binary was not found on PATH.
    """

    name: str
    path: Optional[str]

    @property
    def resolved(self) -> bool:
        """True when setup actually found the binary."""
        return self.path is not None

    def teardown(self, action: Callable[[str], None]) -> None:
        """Run action(self.path) only when setup actually resolved a path.

        A no-op when the guard never resolved the binary -- this is what
        makes a fixture's finally block safe to run unconditionally even
        when setup skipped before establishing anything to tear down.
        Does not suppress any exception `action` itself raises: a genuine
        teardown fault still propagates rather than being hidden behind a
        blanket exception suppression.
        """
        if not self.resolved:
            return
        action(self.path)


def try_resolve_binary(name: str) -> BinaryGuard:
    """Resolve *name* on PATH without skipping.

    Returns a BinaryGuard whose `.resolved` is False (path=None) when the
    binary is not found, and True (path=<absolute path>) when it is. Use
    this when a fixture needs to inspect availability before deciding
    whether/how to skip -- most callers want require_binary() instead.
    """
    found = shutil.which(name)
    if found is None:
        return BinaryGuard(name=name, path=None)
    return BinaryGuard(name=name, path=os.path.realpath(found))


def require_binary(name: str, label: str) -> BinaryGuard:
    """Resolve *name* to an absolute path or perform a narrated skip.

    Returns a BinaryGuard with `.resolved` True and `.path` set to the
    absolute, symlink-resolved binary path. Calls narrate_binary_skip()
    (which raises pytest.skip() and never returns) when *name* is not on
    PATH -- absent is always a narrated skip here, never a silent pass and
    never a plain exception.
    """
    guard = try_resolve_binary(name)
    if not guard.resolved:
        narrate_binary_skip(name, label)
    return guard


def narrate_daemon_unreachable_skip(engine: str, label: str) -> NoReturn:
    """Print a narrated skip line for an unreachable *engine* daemon and
    raise pytest.skip().

    Deliberately worded differently from narrate_binary_skip() so the two
    failure modes never collide in a run log: this one says the daemon did
    not answer, never that the binary is missing -- the binary already
    resolved by the time this is called. Produces:

        [<label>] SKIP — <engine> daemon is not reachable; this test
        requires a running <engine> daemon

    Never returns -- pytest.skip() always raises to unwind the test.
    """
    reason = f"{engine} daemon is not reachable; this test requires a running {engine} daemon"
    print(f"\n[{label}] SKIP — {reason}", flush=True)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip() always raises")  # pragma: no cover


def docker_daemon_reachable(engine: str = "docker", timeout: float = 10) -> bool:
    """Return whether *engine*'s daemon answers `<engine> info` within *timeout*.

    Caller must have already confirmed the engine binary itself resolves on
    PATH (require_docker_daemon() does this by calling require_binary()
    first) -- this function only tells the daemon-reachable fact apart from
    the binary-present fact, it does not re-check the binary. A timeout, a
    non-zero exit, or the engine having vanished mid-check (OSError) are all
    treated as unreachable rather than raised, since "the daemon did not
    answer in time" and "the daemon refused" are both the same actionable
    fact from a test's perspective: skip and name the daemon, not the
    binary.
    """
    try:
        result = subprocess.run([engine, "info"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def require_docker_daemon(engine: str = "docker", label: str = "docker") -> BinaryGuard:
    """Resolve *engine* to an absolute path AND confirm its daemon answers,
    or perform a narrated skip that names whichever of the two facts is
    false.

    Two distinct skip reasons, never blurred into one:
      - *engine* not on PATH at all -> require_binary()'s own narration
        ("<engine> binary not found on PATH; this test requires <engine>").
      - *engine* on PATH but the daemon does not answer ->
        narrate_daemon_unreachable_skip()'s narration ("<engine> daemon is
        not reachable; this test requires a running <engine> daemon").

    Returns a BinaryGuard with `.resolved` True and `.path` set to the
    absolute engine path only when both checks pass.
    """
    guard = require_binary(engine, label=label)
    if not docker_daemon_reachable(engine):
        narrate_daemon_unreachable_skip(engine, label)
    return guard


def teardown_private_tmux(
    guard: BinaryGuard,
    socket_name: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    kill: Optional[Callable[[str], object]] = None,
) -> None:
    """Stop a private tmux server (``tmux -L <socket_name>``) and remove its
    socket file -- the one teardown every private-tmux fixture shares.

    A no-op when *guard* never resolved tmux (setup skipped before a server
    could exist), so a fixture's finally block may call it unconditionally.
    Otherwise it runs ``kill(guard.path)`` -- by default
    ``tmux -L <socket_name> kill-server`` under *env* -- and then, in a
    finally, deletes the socket file at
    ``$TMUX_TMPDIR/tmux-<uid>/<socket_name>`` as *env* resolves it
    (pgai_agent_kanban.testing.tmux_sockets.socket_path). A stopped tmux
    server leaves that file behind; deleting it here is what keeps a run,
    passing, failing or erroring, from adding one per test.

    *env* must be the environment the server was started under (its
    ``TMUX_TMPDIR`` decides where the socket lives); None means this
    process's environment. Pass *kill* when the fixture stops its server
    another way (``run_until_exit`` with a cmdline marker, say); it gets the
    tmux path. An exception from *kill* still propagates -- after the socket
    file is gone.
    """
    from pgai_agent_kanban.testing.tmux_sockets import socket_path

    def _kill_and_unlink(path: str) -> None:
        try:
            if kill is not None:
                kill(path)
            else:
                subprocess.run(
                    [path, "-L", socket_name, "kill-server"],
                    capture_output=True,
                    timeout=10,
                    env=None if env is None else dict(env),
                )
        finally:
            socket_path(socket_name, env).unlink(missing_ok=True)

    guard.teardown(_kill_and_unlink)
