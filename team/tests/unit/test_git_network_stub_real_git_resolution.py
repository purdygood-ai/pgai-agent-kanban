"""
test_git_network_stub_real_git_resolution.py
==============================================
Behavioral coverage for tests/fixtures/git_network_stub.py's
_resolve_unshimmed_git -- the resolver install_git_network_stub() uses to
find the real git binary its generated stub delegates every non-network
verb to.

WHY THIS EXISTS
---------------
install_git_network_stub() is itself a second PATH-shadowing "git"
stand-in, used to prove zero network-capable git calls happen along a code
path under test. Every non-CM wake dispatch already puts a DIFFERENT
PATH-shadowing "git" --
team/scripts/wake/git-guard/git, the "CM is the ONLY toucher of any remote"
shim -- ahead of the real binary. Before _resolve_unshimmed_git existed,
install_git_network_stub() found that shim via a plain PATH lookup
(shutil.which()-shaped), hardcoded it as its own stub's delegation target,
and the shim's OWN real-git fallback resolution then rediscovered the
network stub itself right back on the same PATH -- a two-process exec
cycle (stub -> shim -> stub -> ...) that never reaches a real git process
and never terminates, because each hop replaces the process image (exec)
rather than forking one. Observed as every non-network git call inside a
guarded dispatch (a plain "git tag -l", "git rev-parse", ...) hanging until
the caller's own timeout killed it, most concretely
tests/unit/test_discovery_git_local_only_wake_context.py's three tests.

This file proves the shim skip behaviorally: a synthetic PATH carrying a
guard-shim-shaped executable ahead of a real one is resolved past the shim,
and an unguarded PATH (no shim anywhere) still resolves normally.

Naming: function names describe behavior under test, never a bug ID
(roles/TESTER.md, "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

import pytest

from tests.fixtures.git_network_stub import (
    _GIT_BOUNDARY_GUARD_SHIM_MARKER,
    _resolve_unshimmed_git,
    install_git_network_stub,
)


def _write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(
        path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )


def _write_fake_guard_shim(bin_dir: pathlib.Path) -> pathlib.Path:
    """Writes a "git" executable into *bin_dir* that carries the exact
    marker string team/scripts/wake/git-guard/git carries (as a comment,
    same as the real shim), so _resolve_unshimmed_git must skip it -- and
    would loop forever against it if it did not, since this stand-in exits
    non-zero for anything rather than delegating anywhere."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim_path = bin_dir / "git"
    marker = _GIT_BOUNDARY_GUARD_SHIM_MARKER.decode("ascii")
    _write_executable(
        shim_path,
        f"#!/usr/bin/env bash\n"
        f"# fake git-boundary-guard shim for a test -- marker: {marker}\n"
        f"echo 'FAKE-SHIM-INVOKED -- this must never be the resolved binary' >&2\n"
        f"exit 99\n",
    )
    return shim_path


def test_resolves_past_a_guard_shaped_executable_ahead_on_path(
    tmp_path: pathlib.Path,
) -> None:
    """A "git" carrying the guard's own marker string, first on PATH, is
    skipped; the next "git" on PATH -- one without the marker -- is
    returned instead."""
    shim_dir = tmp_path / "shim_bin"
    real_dir = tmp_path / "real_bin"
    real_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_guard_shim(shim_dir)
    real_git_path = real_dir / "git"
    _write_executable(
        real_git_path,
        "#!/usr/bin/env bash\necho REAL-GIT-INVOKED\nexit 0\n",
    )

    # _resolve_unshimmed_git reads os.environ["PATH"] directly rather than
    # accepting it as an argument, so this test sets PATH the same way the
    # rest of the suite exercises an environment-driven resolver.
    path = f"{shim_dir}{os.pathsep}{real_dir}"
    old_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = path
        resolved = _resolve_unshimmed_git("probe")
    finally:
        os.environ["PATH"] = old_path

    assert resolved == str(real_git_path.resolve()), (
        f"expected the unshimmed real git ({real_git_path}), got {resolved!r}"
    )
    result = subprocess.run(
        [resolved], capture_output=True, text=True, timeout=10
    )
    assert result.stdout.strip() == "REAL-GIT-INVOKED", (
        "resolved binary must be the real one, never the guard-shaped stand-in"
    )


def test_resolves_normally_when_no_shim_is_on_path(tmp_path: pathlib.Path) -> None:
    """With no guard-marker-carrying "git" anywhere on PATH, resolution
    behaves like a plain PATH lookup and returns the first "git" found."""
    real_dir = tmp_path / "real_bin"
    real_dir.mkdir(parents=True, exist_ok=True)
    real_git_path = real_dir / "git"
    _write_executable(
        real_git_path,
        "#!/usr/bin/env bash\necho REAL-GIT-INVOKED\nexit 0\n",
    )

    old_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(real_dir)
        resolved = _resolve_unshimmed_git("probe")
    finally:
        os.environ["PATH"] = old_path

    assert resolved == str(real_git_path.resolve())


def test_skips_the_shim_and_finds_no_further_candidate_skips(
    tmp_path: pathlib.Path,
) -> None:
    """When every "git" on PATH carries the guard marker, resolution finds
    none and performs the shared guard's own narrated skip
    (pytest.skip.Exception -- the same outcome require_binary() itself
    raises for a genuinely absent binary) rather than ever returning a
    shim path or hanging. Caught explicitly (not via a bare
    `pytest.raises(Exception)`, which pytest.skip()'s BaseException
    subclass would slip past, turning this assertion into a silent no-op
    reported as SKIPPED instead of PASSED)."""
    shim_dir = tmp_path / "shim_bin"
    _write_fake_guard_shim(shim_dir)

    old_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(shim_dir)
        with pytest.raises(pytest.skip.Exception) as excinfo:
            _resolve_unshimmed_git("probe")
    finally:
        os.environ["PATH"] = old_path

    assert "git binary not found on PATH" in str(excinfo.value)


def test_install_git_network_stub_delegates_to_the_unshimmed_binary(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: with a guard-shaped "git" ahead of a real one on PATH,
    install_git_network_stub()'s generated stub still completes a
    non-network git call (through the real binary) instead of looping
    forever between itself and the shim-shaped stand-in."""
    shim_dir = tmp_path / "shim_bin"
    _write_fake_guard_shim(shim_dir)

    repo = tmp_path / "repo"
    repo.mkdir()
    old_path = os.environ.get("PATH", "")
    try:
        # A real git must still be reachable to actually run the probe
        # below; prepend the fake shim ahead of whatever real git this
        # test host already has on PATH, exactly like a guarded wake
        # dispatch does.
        os.environ["PATH"] = f"{shim_dir}{os.pathsep}{old_path}"

        stub_bin = tmp_path / "stub-bin"
        log_file = tmp_path / "network-calls.log"
        overrides = install_git_network_stub(stub_bin, log_file)

        env = dict(os.environ)
        env.update(overrides)
        result = subprocess.run(
            ["git", "init", "-q", str(repo)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        os.environ["PATH"] = old_path

    assert result.returncode == 0, (
        f"a non-network git call through the stub must complete, not hang or "
        f"loop.\nstdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert (repo / ".git").is_dir()
