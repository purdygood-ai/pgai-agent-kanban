"""
test_binary_guard_contract.py
==============================
Self-test for the shared external-binary availability guard
(tests/fixtures/binary_guard.py).

Background: before this guard existed, four tmux-using test modules each
carried their own copy of the same three-part contract -- an availability
probe, a skip narration, and a resolver that tied the two together -- and
each fixture's teardown reached for the resolved binary again without
checking whether setup ever found it. This module seeds each way that
contract can be got wrong and proves the shared guard notices: a call site
that skips the guard entirely, a guard whose absent-binary branch raises
instead of skipping, and a teardown that reaches for a binary setup never
resolved. Each seeded violation is paired with a companion test showing the
same scenario succeed once the seed is replaced with the shared guard.

Violations 1-3 exercise the guard's own logic directly against a binary
name guaranteed never to exist, so they are deterministic on every host
regardless of what happens to be on PATH. Violation 4 seeds the same
"unguarded call fails loud" scenario against the two binaries the estate
actually shells out to at scale -- docker and git -- rather than a synthetic
name, with PATH scrubbed for just that test so the seed is deterministic
even on an authoring host where both binaries are normally installed. The
docker half also exercises require_docker_daemon() specifically, since that
is the daemon-probing guard entry point layered on top of
require_binary().
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures.binary_guard import (
    BinaryGuard,
    require_binary,
    require_docker_daemon,
    try_resolve_binary,
)

# A binary name guaranteed absent from every PATH this suite runs under --
# never installed by any package this project depends on.
_ABSENT_BINARY = "definitely-not-a-real-binary-pgai-tests-never-install-this"


# ---------------------------------------------------------------------------
# Violation 1 -- a fixture that calls the external binary with no guard at all
# ---------------------------------------------------------------------------


def test_calling_an_absent_binary_with_no_guard_fails_loud() -> None:
    """
    Seeded violation: a call site that skips the guard entirely and invokes
    the (absent) binary directly. This must fail loud -- a raised
    FileNotFoundError naming the binary, not a silent pass and not a
    graceful skip -- because nothing ever checked availability first.
    """
    with pytest.raises(FileNotFoundError) as excinfo:
        subprocess.run([_ABSENT_BINARY, "-V"], capture_output=True)
    assert _ABSENT_BINARY in str(excinfo.value), (
        "expected the FileNotFoundError to name the missing binary; "
        f"got: {excinfo.value!r}"
    )


def test_calling_the_same_absent_binary_through_the_guard_skips_instead_of_erroring() -> None:
    """
    Seed removed: routing the same absent binary through require_binary()
    turns the loud FileNotFoundError above into a narrated pytest.skip --
    present means exercise, absent means a narrated skip, never an error.
    """
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_binary(_ABSENT_BINARY, label="binary-guard-contract")
    assert _ABSENT_BINARY in str(excinfo.value), (
        f"expected the skip reason to name the missing binary; got: {excinfo.value!r}"
    )


# ---------------------------------------------------------------------------
# Violation 2 -- a guard whose absent-binary branch raises instead of skipping
# ---------------------------------------------------------------------------


def _broken_require_binary_that_raises(name: str) -> str:
    """The seeded violation: an absent-binary branch that raises a plain
    exception instead of performing a narrated pytest.skip."""
    guard = try_resolve_binary(name)
    if not guard.resolved:
        raise RuntimeError(f"{name} binary not found on PATH")
    return guard.path


def test_a_guard_whose_absent_binary_branch_raises_fails_loud() -> None:
    """
    Seeded violation: an absent-binary branch that raises a plain exception
    breaks the "absent means a narrated skip, never an error" half of the
    contract -- the caller sees a hard failure instead of a graceful skip.
    """
    with pytest.raises(RuntimeError) as excinfo:
        _broken_require_binary_that_raises(_ABSENT_BINARY)
    assert _ABSENT_BINARY in str(excinfo.value), (
        f"expected the RuntimeError to name the missing binary; got: {excinfo.value!r}"
    )


def test_the_shared_guard_skips_instead_of_raising_for_the_same_absent_binary() -> None:
    """
    Seed removed: the shared require_binary() performs the narrated skip
    that the broken variant above failed to perform, for the identical
    input.
    """
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_binary(_ABSENT_BINARY, label="binary-guard-contract")
    assert _ABSENT_BINARY in str(excinfo.value)


# ---------------------------------------------------------------------------
# Violation 3 -- a teardown that invokes the binary unconditionally after a
# skipped setup
# ---------------------------------------------------------------------------


def test_an_unconditional_teardown_after_a_skipped_setup_fails_loud() -> None:
    """
    Seeded violation: setup never resolved the binary (guard.resolved is
    False), but the teardown reaches for guard.path anyway instead of
    checking first -- exactly the historical defect the guard replaces: "a
    kill-server invocation issued against a binary that is not there raises
    where a skip was owed." Here the raise is a TypeError from passing None
    as a subprocess argv entry -- a real, verified consequence of guard.path
    being None, not an assumed one.
    """
    guard = try_resolve_binary(_ABSENT_BINARY)
    assert not guard.resolved

    def _unconditional_teardown() -> None:
        subprocess.run([guard.path, "-V"], capture_output=True)

    with pytest.raises(TypeError) as excinfo:
        _unconditional_teardown()
    assert "NoneType" in str(excinfo.value), (
        f"expected the TypeError to complain about the None path; got: {excinfo.value!r}"
    )


def test_binary_guard_teardown_is_a_no_op_after_a_skipped_setup() -> None:
    """
    Seed removed: routing the same teardown call through
    BinaryGuard.teardown() makes it a safe no-op when setup never resolved
    the binary -- no exception, and the caller's teardown callable is never
    invoked at all.
    """
    guard = try_resolve_binary(_ABSENT_BINARY)
    assert not guard.resolved

    invoked: list[str] = []
    guard.teardown(lambda path: invoked.append(path))

    assert invoked == [], (
        "BinaryGuard.teardown() must not invoke its callable when the "
        "guard never resolved a path"
    )


# ---------------------------------------------------------------------------
# Violation 4 -- an unguarded call to docker or git, seeded absent via a
# scrubbed PATH rather than a synthetic binary name. Proves the same
# fail-loud-then-skip contract holds for the two real binaries the estate
# guards at scale, not only for the synthetic name used in Violations 1-3.
# ---------------------------------------------------------------------------


def test_calling_docker_with_no_guard_fails_loud_when_docker_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Seeded violation: a call site that skips the guard entirely and invokes
    docker directly. PATH is scrubbed to an empty directory for this test
    only, so the seed is deterministic whether or not docker happens to be
    installed on the host running this suite -- the same PATH-shaping
    technique the full unit/integration runs use to rehearse a
    docker-absent host, applied here at the single-test scope. Must fail
    loud: a raised FileNotFoundError naming "docker", not a silent pass and
    not a graceful skip, because nothing ever checked availability first.
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError) as excinfo:
        subprocess.run(["docker", "info"], capture_output=True)
    assert "docker" in str(excinfo.value), (
        f"expected the FileNotFoundError to name docker; got: {excinfo.value!r}"
    )


def test_calling_the_same_absent_docker_through_the_guard_skips_instead_of_erroring(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Seed removed: routing the same scrubbed-PATH call through
    require_docker_daemon() turns the loud FileNotFoundError above into a
    narrated pytest.skip naming "docker" as the missing binary -- the
    absent-binary check inside require_docker_daemon() runs (and reports)
    before the daemon-reachability check ever gets a chance to, so the skip
    reason correctly says "binary not found", never "daemon unreachable".
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_docker_daemon(label="binary-guard-contract")
    assert "docker" in str(excinfo.value), (
        f"expected the skip reason to name docker; got: {excinfo.value!r}"
    )


def test_docker_present_but_daemon_unreachable_skips_naming_the_daemon_not_the_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    The distinction the SOP build-outcome doctrine depends on: "the binary
    is not installed" and "the binary is installed but the daemon is not
    answering" are different facts, and only the second is an environmental
    gap an operator can fix by starting the daemon rather than installing
    anything. Seeds a fake "docker" binary that resolves on PATH but exits
    non-zero on `docker info` (a stand-in for a stopped daemon or an
    unreachable DOCKER_HOST) and asserts require_docker_daemon() skips
    naming the daemon as unreachable -- never claiming the binary is
    missing, which would send an operator to the wrong remedy.
    """
    fake_bin = tmp_path / "fake-docker-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_docker_daemon(label="binary-guard-contract")
    reason = str(excinfo.value)
    assert "daemon" in reason and "not reachable" in reason, (
        f"expected the skip reason to say the daemon is unreachable; got: {reason!r}"
    )
    assert "not found on PATH" not in reason, (
        f"docker resolved on PATH here; the skip must not claim it is missing: {reason!r}"
    )


def test_calling_git_with_no_guard_fails_loud_when_git_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Seeded violation: a call site that skips the guard entirely and invokes
    git directly. PATH is scrubbed to an empty directory for this test only
    (same technique as the docker half above), so the seed is deterministic
    regardless of whether git happens to be installed on the host running
    this suite. Must fail loud: a raised FileNotFoundError naming "git".
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError) as excinfo:
        subprocess.run(["git", "status"], capture_output=True)
    assert "git" in str(excinfo.value), (
        f"expected the FileNotFoundError to name git; got: {excinfo.value!r}"
    )


def test_calling_the_same_absent_git_through_the_guard_skips_instead_of_erroring(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Seed removed: routing the same scrubbed-PATH call through
    require_binary() turns the loud FileNotFoundError above into a narrated
    pytest.skip naming "git" as the missing binary.
    """
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_binary("git", label="binary-guard-contract")
    assert "git" in str(excinfo.value), (
        f"expected the skip reason to name git; got: {excinfo.value!r}"
    )


# ---------------------------------------------------------------------------
# Guard behaviour when the binary IS present -- present means exercise, and
# .teardown() must still run in that case rather than becoming a permanent
# no-op.
# ---------------------------------------------------------------------------


def test_binary_guard_teardown_invokes_the_callable_once_resolved(tmp_path) -> None:
    """
    A guard that DID resolve a binary must still run its teardown callable
    -- BinaryGuard.teardown() is conditional on resolution, not a blanket
    no-op. Uses a synthetic always-present "binary" (a throwaway executable
    file) so this assertion never depends on any real external tool being
    installed on the host running this suite.
    """
    fake_binary = tmp_path / "fake-binary"
    fake_binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_binary.chmod(0o755)

    guard = BinaryGuard(name="fake-binary", path=str(fake_binary))
    assert guard.resolved

    invoked: list[str] = []
    guard.teardown(lambda path: invoked.append(path))

    assert invoked == [str(fake_binary)], (
        "BinaryGuard.teardown() must invoke its callable exactly once, "
        "with the resolved path, when the guard did resolve a binary"
    )


def test_binary_guard_teardown_does_not_suppress_a_genuine_fault_from_the_callable() -> None:
    """
    BinaryGuard.teardown() gates on whether setup resolved a binary; it does
    not blanket-suppress whatever the teardown callable itself raises. A
    genuine teardown fault (a hung kill command, a permissions error) must
    still propagate rather than being hidden -- the guard's contract is
    "conditional, not blind" (see the module docstring in binary_guard.py).
    """
    guard = BinaryGuard(name="fake-binary", path="/usr/bin/true")

    def _faulty_teardown(_path: str) -> None:
        raise RuntimeError("simulated genuine teardown fault")

    with pytest.raises(RuntimeError, match="simulated genuine teardown fault"):
        guard.teardown(_faulty_teardown)
