"""
git_network_stub.py
====================
Test-only ``git`` PATH-shadowing interceptor for BUG-0112 dashboard fixtures.

WHY THIS EXISTS
---------------
BUG-0112 requires proof — not just code inspection — that a full dashboard
refresh never reaches the network via git.  The only reliable way to prove
"zero network-capable git invocations" behaviorally is to put a stand-in
``git`` binary first on ``PATH`` that recognizes the network-capable verbs
(``fetch``, ``pull``, ``push``, ``clone``, ``ls-remote``), logs any such
invocation to a file, and returns an offline-safe result WITHOUT ever
touching the network.  Every other git invocation (``tag``, ``log``,
``status``, ``rev-parse``, ``branch``, etc.) delegates unchanged to the real
git binary so callers still see faithful local repository state.

This module formalizes a technique three prior BUG-0112 tickets
(CODER-20260726-002/003/004) each built ad hoc in scratch directories for
their own manual verification.  Promoting it to a shared, importable fixture
module makes the same interception available to permanent pytest coverage
(the network-stub and missing-tree fixtures this ticket adds) instead of
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
import shutil
import stat
from pathlib import Path

# Git subcommands BUG-0112 forbids anywhere under team/scripts/dashboard/.
NETWORK_CAPABLE_VERBS: tuple[str, ...] = (
    "ls-remote",
    "fetch",
    "pull",
    "push",
    "clone",
)


def install_git_network_stub(bin_dir: Path, log_file: Path) -> dict[str, str]:
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

    Returns
    -------
    dict[str, str]
        Environment overrides — a single ``PATH`` key with *bin_dir*
        prepended to the inherited ``PATH`` — for callers to merge into the
        subprocess environment (e.g. ``{**os.environ, **overrides}``).

    Raises
    ------
    RuntimeError
        If no real ``git`` binary can be found on the current ``PATH`` to
        delegate to.  Without a real git to fall back on, every ordinary
        (non-network) git call the dashboard makes would also break,
        producing false-positive test failures unrelated to BUG-0112.
    """
    real_git = shutil.which("git")
    if real_git is None:
        raise RuntimeError(
            "install_git_network_stub: no real 'git' binary found on PATH — "
            "cannot build a delegating stub without one"
        )

    bin_dir.mkdir(parents=True, exist_ok=True)
    stub_path = bin_dir / "git"

    verb_pattern = "|".join(NETWORK_CAPABLE_VERBS)
    stub_script = f"""#!/usr/bin/env bash
# Test-only git interceptor (BUG-0112 network-stub fixture).
# Logs any network-capable invocation ({", ".join(NETWORK_CAPABLE_VERBS)})
# to LOG_FILE and returns an offline-safe failure WITHOUT reaching the
# network. Every other invocation delegates unchanged to the real git binary.
set -u
REAL_GIT={real_git!r}
LOG_FILE={str(log_file)!r}
for _arg in "$@"; do
  case "$_arg" in
    {verb_pattern})
      printf '%s\\n' "git $*" >> "$LOG_FILE"
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
