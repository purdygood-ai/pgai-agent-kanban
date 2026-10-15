"""
test_discovery_git_local_only_wake_context.py
===============================================
Discovery in wake context: behavior unchanged when the local-only guard is
left unset (existing fixtures green).

WHY THIS EXISTS
---------------
A ``PGAI_GIT_LOCAL_ONLY`` guard was added to
``_disc_git_tag_exists`` (team/scripts/lib/discovery.sh) so that render-path
callers (the dashboard) can opt into skipping the ``git ls-remote`` network
fallthrough.  No test in the suite previously exercised
``_disc_git_tag_exists`` directly with a network-observing stub — the
guard's "left unset, behavior is unchanged" half of the contract was
documented but not behaviorally proven.  This closes that gap: it proves
that when ``PGAI_GIT_LOCAL_ONLY`` is left unset — a CM dispatch or an
operator's own shell; discovery.sh's own callers never set the flag
themselves, and the wake layer (team/scripts/lib/wake_guard_wiring.sh)
exports it only for a dispatched role other than cm — a tag absent from
the local repo still falls through to the origin ls-remote check, exactly
as it did before the guard was added.  This is the behavior CM-side
discovery (patch-slot selection, requirements bundling) depends on to
detect already-taken version tags on origin.

A second test proves the render-path guard's other half for contrast:
when the flag IS set, the same call makes zero network attempts.  Together
the two tests bound the guard's behavior in both directions.

HOW NETWORK-CALL DETECTION WORKS
---------------------------------
A PATH-shadowing git stub (tests/fixtures/git_network_stub.py) intercepts
any ``ls-remote``/``fetch``/``pull``/``push``/``clone`` invocation, logs it,
and returns an offline-safe failure without ever reaching a real remote.
Every other git invocation (tag listing, etc.) delegates unchanged to the
real git binary, so ``_disc_git_tag_exists``'s local-tag check still sees
faithful local repository state.

HOW THE ENVIRONMENT IS PINNED
-----------------------------
Every subprocess this module starts -- the ``git`` calls that build the
fixture repository and the ``bash`` probe that calls the function under
test -- receives an environment built from an explicit, named set
(``build_scrubbed_env``: PATH, a scratch HOME, LANG), never a copy of the
test process's own ``os.environ``.  The invoking shell is therefore not an
input to any verdict here: under a non-CM agent wake that shell carries
``PGAI_GIT_LOCAL_ONLY=1`` (exported by team/scripts/lib/wake_guard_wiring.sh)
and puts the git-boundary-guard shim ahead of the real git on PATH, under an
operator's shell it usually does neither, and a test that ran earlier in the
same pytest process may have left any other variable behind.  None of that
reaches the child.

The flag itself is added only by the case that is about it, so "unset" is
structural -- the builder never constructs the name -- rather than a deletion
someone has to remember.  discovery_guards.sh tests the flag with
``[[ -n "${PGAI_GIT_LOCAL_ONLY:-}" ]]``, so "unset" and "empty" are the same
state to the code under test; leaving the name out is the unambiguous way to
reach it.  Each probe also echoes the flag state the child actually saw and
asserts on it before asserting on behavior, so a change that let ambient
state back in fails with a precondition message rather than a misleading
network-call verdict.

A scratch HOME and ``GIT_CONFIG_NOSYSTEM=1`` keep the operator's global and
system git configuration (signing, hooks, URL rewrites) from steering the
fixture repository or the lookup.  Following
docs/test-authoring-guidelines.md, "Subprocess environments are built from
an explicit set, not inherited".
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.git_network_stub import install_git_network_stub
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import BashResult, run_bash


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="discovery-git-local-only-wake-context")

_LIB = "scripts/lib/discovery.sh"

# Printed by every probe ahead of the lookup so a test can assert on the
# flag state the child process actually received.
_FLAG_UNSET_MARKER = "<unset>"


def _explicit_env(
    tmp_path: pathlib.Path,
    *,
    stub_bin: Optional[pathlib.Path] = None,
    local_only_flag: Optional[str] = None,
) -> dict[str, str]:
    """Build the complete environment for one of this module's subprocesses.

    Starts from ``build_scrubbed_env`` (PATH, scratch HOME, LANG) and adds
    only what the scenario is about: the network stub's directory ahead of
    the standard toolchain when *stub_bin* is given, and
    ``PGAI_GIT_LOCAL_ONLY`` when *local_only_flag* is not ``None``.
    ``PGAI_AGENT_KANBAN_TEMP_DIR`` is carried over by name so any scratch
    file discovery.sh's temp helpers create stays under the test temp root
    the parent conftest already chose.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    extra: dict[str, str] = {"GIT_CONFIG_NOSYSTEM": "1"}
    temp_dir = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR")
    if temp_dir:
        extra["PGAI_AGENT_KANBAN_TEMP_DIR"] = temp_dir
    if local_only_flag is not None:
        extra["PGAI_GIT_LOCAL_ONLY"] = local_only_flag
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[stub_bin] if stub_bin is not None else (),
        extra_env=extra,
    )


def _git(repo: pathlib.Path, *args: str) -> None:
    """Run a fixture-setup git command under this module's explicit environment.

    The repository lives under pytest's ``tmp_path``, so its parent provides
    the scratch HOME and the same isolation the probe gets.
    """
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_explicit_env(repo.parent),
    )


def _probe_tag_exists(
    tmp_path: pathlib.Path,
    tag: str,
    repo: pathlib.Path,
    *,
    stub_bin: pathlib.Path,
    local_only_flag: Optional[str],
) -> tuple[str, BashResult]:
    """Call ``_disc_git_tag_exists <tag> <repo>`` in a fresh bash process.

    Returns the flag state the child observed (``_FLAG_UNSET_MARKER`` when
    the name was absent) and the captured result of the lookup itself.
    """
    script = (
        f"printf '%s\\n' \"${{PGAI_GIT_LOCAL_ONLY-{_FLAG_UNSET_MARKER}}}\"; "
        f"source {_LIB} && _disc_git_tag_exists {tag} {repo!s}"
    )
    result = run_bash(
        tmp_path,
        script,
        base_env=_explicit_env(
            tmp_path, stub_bin=stub_bin, local_only_flag=local_only_flag
        ),
    )
    observed_flag = result.stdout.splitlines()[0] if result.stdout else ""
    return observed_flag, result


def _assert_child_saw_flag(
    observed: str, expected: Optional[str], result: BashResult
) -> None:
    """Fail on the precondition, not the behavior, if the flag state leaked."""
    want = _FLAG_UNSET_MARKER if expected is None else expected
    assert observed == want, (
        f"Precondition: the lookup subprocess should have seen "
        f"PGAI_GIT_LOCAL_ONLY={want!r} but saw {observed!r} -- the probe "
        f"environment is no longer built from an explicit set. "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


def _make_repo_without_tag(repo: pathlib.Path) -> None:
    """A real git repo with one commit and NO tags — so the target tag
    lookup always misses locally and (absent the local-only guard) falls
    through to the ls-remote check."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


# ---------------------------------------------------------------------------
# Case 1 — CM dispatch / operator shell (flag unset): ls-remote fires
# ---------------------------------------------------------------------------


def test_flag_unset_still_falls_through_to_ls_remote(
    tmp_path: pathlib.Path,
) -> None:
    """With PGAI_GIT_LOCAL_ONLY unset, a tag absent locally still triggers the
    origin ls-remote fallthrough — the CM-dispatch / operator-shell behavior
    that must remain unchanged by the local-only guard.

    discovery.sh's own callers (discovery_compute_next_patch, PM's ticket
    decomposition) never set PGAI_GIT_LOCAL_ONLY themselves -- under a
    non-CM agent wake the flag arrives from the wake layer's dispatch
    exports, and under a CM wake or an operator's shell it is absent; this
    test proves that leaving it unset preserves the origin network
    fallthrough exactly as it behaved before the guard was added.

    The "unset" state is structural, not inherited: the probe environment
    is built from an explicit set that never names the flag, so a non-CM
    wake that exported it into the test process cannot carry it into the
    child and route the lookup down the local-only branch.
    """
    repo = tmp_path / "dev_tree"
    _make_repo_without_tag(repo)

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    install_git_network_stub(stub_bin, log_file)

    observed_flag, result = _probe_tag_exists(
        tmp_path, "v9.9.9", repo, stub_bin=stub_bin, local_only_flag=None
    )
    _assert_child_saw_flag(observed_flag, None, result)

    # The stub returns a non-zero ls-remote result (simulating "not found on
    # origin either"), so the function itself returns 1 — that is expected
    # and not what this test asserts on.  What matters is that the network
    # call was ATTEMPTED at all.
    assert result.returncode == 1, (
        f"Expected _disc_git_tag_exists to report 'not found' (rc=1); "
        f"got rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )

    assert log_file.exists(), (
        "Expected the network-call log to exist — no ls-remote attempt was "
        "recorded, meaning the wake-context fallthrough did not fire. This "
        "would be a regression: PGAI_GIT_LOCAL_ONLY is unset here, so the "
        "origin fallthrough must still occur."
    )
    logged = log_file.read_text(encoding="utf-8")
    assert "ls-remote" in logged, (
        f"Expected an 'ls-remote' invocation in the network-call log; got: {logged!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — render-path / non-CM wake (flag set): zero network calls
# ---------------------------------------------------------------------------


def test_flag_set_makes_zero_network_calls(tmp_path: pathlib.Path) -> None:
    """With PGAI_GIT_LOCAL_ONLY=1, the same lookup makes zero network calls.

    Documents the other half of the guard's contract for contrast: a
    render-path caller (the dashboard) or a non-CM agent wake that opts in
    never reaches ls-remote, even for a tag that does not exist locally.
    The flag is the one scenario value added to an otherwise explicit
    probe environment, so the verdict is the same whether or not the
    invoking shell already exported it.
    """
    repo = tmp_path / "dev_tree"
    _make_repo_without_tag(repo)

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    install_git_network_stub(stub_bin, log_file)

    observed_flag, result = _probe_tag_exists(
        tmp_path, "v9.9.9", repo, stub_bin=stub_bin, local_only_flag="1"
    )
    _assert_child_saw_flag(observed_flag, "1", result)

    assert result.returncode == 1, (
        f"Expected _disc_git_tag_exists to report 'not found locally' (rc=1) "
        f"under the local-only guard; got rc={result.returncode}"
    )
    assert not log_file.exists(), (
        "Expected zero network calls under PGAI_GIT_LOCAL_ONLY=1, but the "
        f"network-call log was created: {log_file.read_text(encoding='utf-8') if log_file.exists() else ''}"
    )


# ---------------------------------------------------------------------------
# Case 3 — a locally-present tag never needs the network either way
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_only_flag",
    [
        pytest.param(None, id="flag-unset"),
        pytest.param("1", id="flag-set"),
    ],
)
def test_locally_present_tag_never_reaches_network(
    tmp_path: pathlib.Path,
    local_only_flag: Optional[str],
) -> None:
    """A tag that already exists locally short-circuits before any network
    check — true both with and without PGAI_GIT_LOCAL_ONLY. Sanity check
    that the guard change did not alter the already-fast local-hit path.

    "Both" is proven as two pinned cases rather than left to whichever
    state the invoking shell happened to export: the unset case never
    names the variable in the probe environment, the set case adds it.
    """
    repo = tmp_path / "dev_tree"
    _make_repo_without_tag(repo)
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release v1.0.0")

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    install_git_network_stub(stub_bin, log_file)

    observed_flag, result = _probe_tag_exists(
        tmp_path, "v1.0.0", repo, stub_bin=stub_bin, local_only_flag=local_only_flag
    )
    _assert_child_saw_flag(observed_flag, local_only_flag, result)

    assert result.returncode == 0, (
        f"Expected _disc_git_tag_exists to report 'found locally' (rc=0); "
        f"got rc={result.returncode}"
    )
    assert not log_file.exists(), (
        "A locally-present tag must short-circuit before any network check "
        "is attempted."
    )
