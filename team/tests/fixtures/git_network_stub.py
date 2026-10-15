"""
git_network_stub.py
====================
Test-only ``git`` PATH-shadowing interceptor for dashboard render-path fixtures.

WHY THIS EXISTS
---------------
A full dashboard refresh must be proven — not just code-inspected — never to
reach the network via git.  The only reliable way to prove
"zero network-capable git invocations" behaviorally is to put a stand-in
``git`` binary first on ``PATH`` that recognizes the network-capable verbs
(``fetch``, ``pull``, ``push``, ``clone``, ``ls-remote``), logs any such
invocation to a file, and returns an offline-safe result WITHOUT ever
touching the network.  Every other git invocation (``tag``, ``log``,
``status``, ``rev-parse``, ``branch``, etc.) delegates unchanged to the real
git binary so callers still see faithful local repository state.

This module formalizes a technique repeatedly built ad hoc in scratch
directories for one-off manual verification.  Promoting it to a shared, importable fixture
module makes the same interception available to permanent pytest coverage
(the network-stub and missing-tree fixtures built on it) instead of
living only in throwaway reasoning-trace scratch space.

HOW THE STUB DETECTS A NETWORK-CAPABLE INVOCATION
--------------------------------------------------
The stub inspects its own argv token-by-token (not a static text scan) and
flags an invocation the moment ANY argument exactly equals one of the five
network-capable git subcommand names.  This mirrors real git argument
parsing more faithfully than a regex over the command line: it fires for
``git -C "$dev_tree" ls-remote --tags origin``, ``git fetch origin --tags
--quiet``, etc., using the identical detection semantics regardless of
leading flags or path arguments.

USAGE
-----
    from tests.fixtures.git_network_stub import install_git_network_stub

    def test_something(tmp_path):
        log_file = tmp_path / "git-network-calls.log"
        env_overrides = install_git_network_stub(tmp_path / "stub-bin", log_file)
        result = subprocess.run([...], env={**os.environ, **env_overrides})
        assert log_file.read_text() == ""   # zero network calls
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tests.fixtures.binary_guard import narrate_binary_skip

# Git subcommands forbidden anywhere under team/scripts/dashboard/.
NETWORK_CAPABLE_VERBS: tuple[str, ...] = (
    "ls-remote",
    "fetch",
    "pull",
    "push",
    "clone",
)

# A byte string present in team/scripts/wake/git-guard/git (the "CM is the
# ONLY toucher of any remote" shim -- team/scripts/lib/git_boundary_guard.sh)
# and nowhere in a real git binary. Every non-CM wake dispatch puts that
# shim's directory ahead of the real git on PATH, so a plain
# shutil.which("git")-style lookup run from inside such a dispatch resolves
# the shim, not the real binary -- see _resolve_unshimmed_git for why this
# stub cannot use one.
_GIT_BOUNDARY_GUARD_SHIM_MARKER = b"PGAI_GIT_GUARD_DEPTH"


def _resolve_unshimmed_git(label: str) -> str:
    """Return the first git-guard-shim-free ``git`` found on ``PATH``.

    ``require_binary("git", ...)`` (a plain PATH lookup) is the wrong tool
    here specifically because this fixture is itself a second PATH-shadowing
    "git" stand-in: when the git-boundary-guard shim (see the module-level
    marker above) is ahead of the real binary on PATH -- true for every
    non-CM wake dispatch -- a plain lookup resolves the SHIM as "the real
    git", and this stub's generated script then hands every non-network
    verb straight back to that shim. The shim's own real-git resolution, in
    turn, walks the SAME PATH this stub was built from and can rediscover
    this stub ahead of any genuine git install -- a two-process exec cycle
    (stub -> shim -> stub -> ...) that never reaches a real git process and
    never terminates on its own, because each hop replaces the process
    image (exec) rather than forking one.

    This walks PATH by hand instead, skipping every executable named "git"
    whose content carries the shim's marker string (real git binaries are
    ELF executables well past a megabyte; this shim is a shell script a
    few KiB long, so reading a candidate whole before deciding is cheap
    for the one case that needs the check and never reached for the
    other), and returns the first remaining candidate's real
    (symlink-resolved) path. Falls through to require_binary()'s narrated
    skip (identical wording and behavior to every other guarded binary in
    this test estate) only when PATH carries no unshimmed "git" at all.
    """
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(entry, "git") if entry else "git"
        if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
            continue
        try:
            with open(candidate, "rb") as fh:
                content = fh.read()
        except OSError:
            continue
        if _GIT_BOUNDARY_GUARD_SHIM_MARKER in content:
            continue
        return os.path.realpath(candidate)

    narrate_binary_skip("git", label)
    raise AssertionError("unreachable: narrate_binary_skip() always raises")  # pragma: no cover


def install_git_network_stub(
    bin_dir: Path,
    log_file: Path,
    *,
    succeed_verbs: tuple[str, ...] = (),
) -> dict[str, str]:
    """Write a network-intercepting ``git`` stub into *bin_dir*.

    Parameters
    ----------
    bin_dir:
        Directory to create (if absent) and place the stub ``git`` script
        into.  Caller should pass a path under pytest's ``tmp_path`` — never
        a bare ``/tmp`` path.
    log_file:
        Path the stub appends one line to per intercepted network-capable
        invocation.  The file is NOT pre-created here; callers assert on its
        absence or empty content to prove zero network calls, and on its
        content when a violation is deliberately planted.
    succeed_verbs:
        Network-capable verbs (a subset of ``NETWORK_CAPABLE_VERBS``) the
        stub answers with exit 0 instead of 1. They are still logged and
        still never reach any network -- the stub does nothing but log them.
        For a script under test that must get past a ``fetch`` to reach the
        ``push`` the test asserts on. Default: every network verb fails.

    Returns
    -------
    dict[str, str]
        Environment overrides — a single ``PATH`` key with *bin_dir*
        prepended to the inherited ``PATH`` — for callers to merge into the
        subprocess environment (e.g. ``{**os.environ, **overrides}``).

    Skips (via the shared availability guard)
    ------------------------------------------
    When no unshimmed ``git`` binary is on the current ``PATH`` to delegate
    to (see _resolve_unshimmed_git). Without a real git to fall back on,
    every ordinary (non-network) git call the dashboard makes would also
    break, producing false-positive test failures that have nothing to do
    with what the caller is actually checking -- absent means a narrated
    skip here, consistent with every other guarded binary in this test
    estate, never a raised exception.
    """
    unknown = sorted(set(succeed_verbs) - set(NETWORK_CAPABLE_VERBS))
    if unknown:
        raise ValueError(
            f"succeed_verbs {unknown} are not network-capable verbs; "
            f"choose from {list(NETWORK_CAPABLE_VERBS)}"
        )
    real_git = _resolve_unshimmed_git("git-network-stub")

    bin_dir.mkdir(parents=True, exist_ok=True)
    stub_path = bin_dir / "git"

    verb_pattern = "|".join(NETWORK_CAPABLE_VERBS)
    succeed_pattern = "|".join(succeed_verbs) or "''"
    stub_script = f"""#!/usr/bin/env bash
# Test-only git interceptor (network-stub fixture).
# Logs any network-capable invocation ({", ".join(NETWORK_CAPABLE_VERBS)})
# to LOG_FILE and returns WITHOUT reaching the network: exit 0 for a verb in
# the succeed list ({", ".join(succeed_verbs) or "none"}), else an
# offline-safe failure. Every other invocation delegates unchanged to the
# real git binary.
set -u
REAL_GIT={real_git!r}
LOG_FILE={str(log_file)!r}
for _arg in "$@"; do
  case "$_arg" in
    {verb_pattern})
      printf '%s\\n' "git $*" >> "$LOG_FILE"
      case "$_arg" in
        {succeed_pattern}) exit 0 ;;
      esac
      exit 1
      ;;
  esac
done
exec "$REAL_GIT" "$@"
"""
    stub_path.write_text(stub_script, encoding="utf-8")
    stub_path.chmod(
        stub_path.stat().st_mode
        | stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
    )

    existing_path = os.environ.get("PATH", "")
    return {"PATH": f"{bin_dir}{os.pathsep}{existing_path}"}
