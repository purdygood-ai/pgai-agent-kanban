"""
test_discovery_git_local_only_wake_context.py
===============================================
BUG-0112 acceptance #3: "Discovery in wake context: behavior unchanged
(existing fixtures green)."

WHY THIS EXISTS
---------------
CODER-20260726-002 added a ``PGAI_GIT_LOCAL_ONLY`` guard to
``_disc_git_tag_exists`` (team/scripts/lib/discovery.sh) so that render-path
callers (the dashboard) can opt into skipping the ``git ls-remote`` network
fallthrough.  No test in the suite previously exercised
``_disc_git_tag_exists`` directly with a network-observing stub — the
guard's "left unset, behavior is unchanged" half of the contract was
documented but not behaviorally proven.  This closes that gap: it proves
that when ``PGAI_GIT_LOCAL_ONLY`` is left unset (the wake-context / agent-side
default — discovery.sh's own callers never set this flag), a tag absent
from the local repo still falls through to the origin ls-remote check,
exactly as it did before BUG-0112.  This is the behavior wake-context
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
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.fixtures.git_network_stub import install_git_network_stub
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/discovery.sh"


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
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
# Case 1 — wake-context (flag unset): behavior unchanged, ls-remote fires
# ---------------------------------------------------------------------------


def test_flag_unset_still_falls_through_to_ls_remote(tmp_path: pathlib.Path) -> None:
    """With PGAI_GIT_LOCAL_ONLY unset, a tag absent locally still triggers the
    origin ls-remote fallthrough — the wake-context / agent-side behavior
    BUG-0112 requires to remain unchanged.

    discovery.sh's own callers (discovery_compute_next_patch, PM's ticket
    decomposition) never set PGAI_GIT_LOCAL_ONLY; this test proves that
    leaving it unset preserves the pre-BUG-0112 network fallthrough exactly.
    """
    repo = tmp_path / "dev_tree"
    _make_repo_without_tag(repo)

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    env_overrides = install_git_network_stub(stub_bin, log_file)

    result = run_bash(
        tmp_path,
        f"source {_LIB} && _disc_git_tag_exists v9.9.9 {repo!s}",
        extra_env=env_overrides,
    )

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
        "pre-BUG-0112 origin fallthrough must still occur."
    )
    logged = log_file.read_text(encoding="utf-8")
    assert "ls-remote" in logged, (
        f"Expected an 'ls-remote' invocation in the network-call log; got: {logged!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — render-path (flag set): zero network calls, for contrast
# ---------------------------------------------------------------------------


def test_flag_set_makes_zero_network_calls(tmp_path: pathlib.Path) -> None:
    """With PGAI_GIT_LOCAL_ONLY=1, the same lookup makes zero network calls.

    Documents the other half of the guard's contract for contrast: a
    render-path caller (the dashboard) that opts in never reaches ls-remote,
    even for a tag that does not exist locally.
    """
    repo = tmp_path / "dev_tree"
    _make_repo_without_tag(repo)

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    env_overrides = install_git_network_stub(stub_bin, log_file)
    env_overrides["PGAI_GIT_LOCAL_ONLY"] = "1"

    result = run_bash(
        tmp_path,
        f"source {_LIB} && _disc_git_tag_exists v9.9.9 {repo!s}",
        extra_env=env_overrides,
    )

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


def test_locally_present_tag_never_reaches_network(tmp_path: pathlib.Path) -> None:
    """A tag that already exists locally short-circuits before any network
    check — true both with and without PGAI_GIT_LOCAL_ONLY. Sanity check
    that the guard change did not alter the already-fast local-hit path.
    """
    repo = tmp_path / "dev_tree"
    _make_repo_without_tag(repo)
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release v1.0.0")

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    env_overrides = install_git_network_stub(stub_bin, log_file)

    result = run_bash(
        tmp_path,
        f"source {_LIB} && _disc_git_tag_exists v1.0.0 {repo!s}",
        extra_env=env_overrides,
    )

    assert result.returncode == 0, (
        f"Expected _disc_git_tag_exists to report 'found locally' (rc=0); "
        f"got rc={result.returncode}"
    )
    assert not log_file.exists(), (
        "A locally-present tag must short-circuit before any network check "
        "is attempted."
    )
