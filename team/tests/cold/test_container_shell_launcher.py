"""
test_container_shell_launcher.py
==================================
Behavioral unit tests for team/scripts/container-shell.sh — the launcher
that starts the pgai-agent-kanban workspace container.

Covers the launcher's own contract, distinct from the library-level
resolve_docker_exe coverage in test_docker_engine_resolver.py:

  1. --help exits 0 and documents every flag.
  2. Required-path validation: missing/invalid PGAI_AGENT_KANBAN_ROOT_PATH
     (or --kanban-root) and missing/invalid $HOME both fail loud, naming
     the offending value.
  3. --mount validation and unknown-flag handling exit 2 (usage error).
  4. Engine selection end-to-end: the resolved docker_exe (default, a
     configured value via kanban.cfg [docker], or a fail-loud misconfigured
     value) drives which binary the launcher execs, and with what mounts
     and env.

Tests never invoke a real container engine. A fake docker/podman stub script
is placed first on PATH; because the launcher `exec`s the resolved engine,
the stub's own stdout becomes the subprocess's final stdout, letting tests
assert on the assembled `run` invocation without touching a real daemon.

This module is NOT collected by run-unit-tests.sh or run-integration-tests.sh
-- it lives under team/tests/cold/, a directory neither gated runner's pytest
argv reaches. It runs only via team/scripts/run-cold-build-tests.sh (invoked
on demand through team/scripts/deep-verify.sh), alongside the tree's other
container-launching coverage, never in the fast loop every CODER runs on
every ticket.

Test naming describes behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import os
import pathlib
import stat

from tests.unit.shell_harness import run_bash

_LAUNCHER = "scripts/container-shell.sh"

# The launcher's zero-flag default tag. Matches the tag the documented
# `docker build -f docker/debian/Dockerfile -t ... .` command actually
# produces (docs/docker.md), so the out-of-the-box invocation asks the
# engine for an image a real build command produces.
_DEFAULT_IMAGE_TAG = "pgai-agent-kanban-debian:latest"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_fake_engine(bin_dir: pathlib.Path, name: str) -> None:
    """Create an executable stub named *name* that echoes its own argv and exits 0.

    Because the launcher execs the resolved engine binary, the stub's output
    becomes the subprocess's final observable output — a lightweight
    substitute for a real docker/podman invocation.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "FAKE_ENGINE={name}"\n'
        'for arg in "$@"; do printf "ARG:%s\\n" "$arg"; done\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _base_env(
    tmp_path: pathlib.Path,
    *,
    kanban_root: pathlib.Path | None,
    home: pathlib.Path | None,
    fake_bin_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    """Build an extra_env dict for a launcher invocation.

    kanban_root=None or home=None means "leave unset" — callers assert on
    the resulting fail-loud behavior. When set, PATH is prefixed with
    fake_bin_dir (when given) so the launcher's engine resolution finds only
    the fake stub, never a real docker/podman on the host.
    """
    env: dict[str, str] = {}
    if kanban_root is not None:
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    if home is not None:
        env["HOME"] = str(home)
    if fake_bin_dir is not None:
        env["PATH"] = f"{fake_bin_dir}:{os.environ.get('PATH', '')}"
    return env


def _unset_script(*names: str) -> str:
    """Return a bash `unset` prefix for the given variable names."""
    return "unset " + " ".join(names) + "; "


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_flag_exits_zero_and_documents_every_option(tmp_path: pathlib.Path) -> None:
    """--help exits 0 and documents each accepted flag."""
    result = run_bash(tmp_path, f"bash {_LAUNCHER} --help")

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    usage = result.stderr  # usage() writes to stderr in this script
    for expected in ("--image-tag", "--mount", "--kanban-root", "--help"):
        assert expected in usage, f"usage text missing {expected!r}:\n{usage}"
    assert _DEFAULT_IMAGE_TAG in usage, (
        f"usage text does not document the corrected default tag {_DEFAULT_IMAGE_TAG!r}:\n{usage}"
    )


def test_short_help_flag_exits_zero(tmp_path: pathlib.Path) -> None:
    """-h behaves identically to --help."""
    result = run_bash(tmp_path, f"bash {_LAUNCHER} -h")
    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Usage errors
# ---------------------------------------------------------------------------


def test_unknown_flag_exits_two(tmp_path: pathlib.Path) -> None:
    """An unrecognized flag is a usage error (exit 2), not a silent no-op."""
    result = run_bash(tmp_path, f"bash {_LAUNCHER} --not-a-real-flag")
    assert result.returncode == 2, (
        f"expected exit 2; got {result.returncode}\nstderr: {result.stderr!r}"
    )


def test_mount_value_without_colon_exits_two(tmp_path: pathlib.Path) -> None:
    """--mount value missing the ':' separator is a usage error."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --mount not-a-valid-mount-spec",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home),
    )
    assert result.returncode == 2, (
        f"expected exit 2; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "--mount" in result.stderr, f"error does not name --mount:\n{result.stderr!r}"


# ---------------------------------------------------------------------------
# Required-path validation
# ---------------------------------------------------------------------------


def test_missing_kanban_root_exits_one_naming_the_variable(tmp_path: pathlib.Path) -> None:
    """No PGAI_AGENT_KANBAN_ROOT_PATH and no --kanban-root -> exit 1, variable named."""
    home = tmp_path / "home"
    home.mkdir()

    result = run_bash(
        tmp_path,
        _unset_script("PGAI_AGENT_KANBAN_ROOT_PATH") + f"bash {_LAUNCHER}",
        extra_env={"HOME": str(home)},
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "PGAI_AGENT_KANBAN_ROOT_PATH" in result.stderr, (
        f"variable name not named in stderr:\n{result.stderr!r}"
    )


def test_kanban_root_pointing_at_absent_directory_exits_one_naming_it(
    tmp_path: pathlib.Path,
) -> None:
    """--kanban-root pointing at a nonexistent directory -> exit 1, path named."""
    home = tmp_path / "home"
    home.mkdir()
    missing_root = tmp_path / "does-not-exist"

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {missing_root}",
        extra_env={"HOME": str(home)},
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert str(missing_root) in result.stderr, (
        f"missing root path not named in stderr:\n{result.stderr!r}"
    )


def test_missing_home_exits_one_naming_it(tmp_path: pathlib.Path) -> None:
    """A valid kanban root but no $HOME -> exit 1, $HOME named."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        _unset_script("HOME") + f"bash {_LAUNCHER} --kanban-root {kanban_root}",
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "HOME" in result.stderr, f"$HOME not named in stderr:\n{result.stderr!r}"


def test_home_pointing_at_absent_directory_exits_one_naming_it(
    tmp_path: pathlib.Path,
) -> None:
    """$HOME set to a nonexistent directory -> exit 1, path named."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    missing_home = tmp_path / "no-such-home"

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root}",
        extra_env={"HOME": str(missing_home)},
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert str(missing_home) in result.stderr, (
        f"missing HOME path not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Engine selection — default (docker)
# ---------------------------------------------------------------------------


def test_default_engine_is_docker_when_unconfigured(tmp_path: pathlib.Path) -> None:
    """No kanban.cfg [docker] section -> the launcher execs "docker run ..."."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root}",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "FAKE_ENGINE=docker" in result.stdout, (
        f"launcher did not exec the resolved docker engine:\n{result.stdout!r}"
    )
    assert "ARG:run" in result.stdout, f"engine not invoked with 'run':\n{result.stdout!r}"


def test_default_engine_mounts_kanban_root_and_home_at_matching_paths(
    tmp_path: pathlib.Path,
) -> None:
    """The assembled run invocation mounts $HOME and the kanban root at matching paths."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root}",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert f"ARG:{home}:{home}" in result.stdout, (
        f"HOME mount missing or mismatched:\n{result.stdout!r}"
    )
    assert f"ARG:{kanban_root}:{kanban_root}" in result.stdout, (
        f"kanban-root mount missing or mismatched:\n{result.stdout!r}"
    )
    assert f"ARG:PGAI_AGENT_KANBAN_ROOT_PATH={kanban_root}" in result.stdout, (
        f"PGAI_AGENT_KANBAN_ROOT_PATH env not passed through with the resolved path:\n"
        f"{result.stdout!r}"
    )


def test_extra_mount_flag_is_forwarded_to_the_engine(tmp_path: pathlib.Path) -> None:
    """A repeatable --mount flag is translated into an extra -v flag on the run invocation."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root} --mount /extra/host:/extra/container",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "ARG:/extra/host:/extra/container" in result.stdout, (
        f"extra mount not forwarded:\n{result.stdout!r}"
    )


def test_image_tag_flag_overrides_the_default(tmp_path: pathlib.Path) -> None:
    """--image-tag overrides the default image and is passed as the final run argument."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root} --image-tag custom-image:v9",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "ARG:custom-image:v9" in result.stdout, (
        f"custom image tag not forwarded:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Engine selection — configured via kanban.cfg [docker]
# ---------------------------------------------------------------------------


def test_configured_podman_is_used_when_present_on_path(tmp_path: pathlib.Path) -> None:
    """kanban.cfg [docker] docker_exe = podman -> the launcher execs "podman run ..."."""
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text(
        "[docker]\ndocker_exe = podman\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "podman")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root}",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "FAKE_ENGINE=podman" in result.stdout, (
        f"launcher did not exec the configured podman engine:\n{result.stdout!r}"
    )


def test_configured_invalid_engine_fails_loud_naming_key_and_value(
    tmp_path: pathlib.Path,
) -> None:
    """A configured docker_exe that has no matching PATH entry fails loud before exec.

    No fake engine binary is created — the configured name must not resolve.
    The launcher must exit non-zero and name both the offending config key
    and value, never silently fall back to the "docker" default.
    """
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text(
        "[docker]\ndocker_exe = nonexistent-container-engine-xyz\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    empty_bin = tmp_path / "empty_fakebin"
    empty_bin.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root}",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=empty_bin),
    )

    assert result.returncode != 0, (
        f"expected a non-zero exit; got {result.returncode}\nstdout: {result.stdout!r}"
    )
    assert "docker_exe" in result.stderr, (
        f"error does not name the docker_exe key:\n{result.stderr!r}"
    )
    assert "nonexistent-container-engine-xyz" in result.stderr, (
        f"error does not name the offending value:\n{result.stderr!r}"
    )
    assert "FAKE_ENGINE" not in result.stdout, (
        f"launcher execed an engine despite the configured value being invalid:\n"
        f"{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Zero-flag launch: default tag, --rm, and relaunch-same-board
# ---------------------------------------------------------------------------


def _flag_values(stdout: str, *flags: str) -> list[str]:
    """Extract the values following any of *flags* from a fake-engine ARG: transcript.

    The fake engine stub echoes one "ARG:<value>" line per argv element, in
    order. This walks that transcript and collects the value immediately
    following each occurrence of one of the named flags (e.g. "-v", "-e"),
    which is how mount and env values are recovered for comparison.
    """
    lines = [line[len("ARG:") :] for line in stdout.splitlines() if line.startswith("ARG:")]
    values: list[str] = []
    capture_next = False
    for arg in lines:
        if capture_next:
            values.append(arg)
            capture_next = False
            continue
        if arg in flags:
            capture_next = True
    return values


def test_zero_flag_launch_uses_the_corrected_default_tag(tmp_path: pathlib.Path) -> None:
    """No flags at all: the launcher resolves both paths from the environment alone
    and assembles a run invocation against the tag a documented build command
    actually produces, with no flag and no ad-hoc export required.
    """
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER}",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert f"ARG:{_DEFAULT_IMAGE_TAG}" in result.stdout, (
        f"zero-flag launch did not use the corrected default tag {_DEFAULT_IMAGE_TAG!r}:\n"
        f"{result.stdout!r}"
    )
    assert f"ARG:{home}:{home}" in result.stdout, (
        f"zero-flag launch did not resolve HOME as a mount:\n{result.stdout!r}"
    )
    assert f"ARG:{kanban_root}:{kanban_root}" in result.stdout, (
        f"zero-flag launch did not resolve the kanban root as a mount:\n{result.stdout!r}"
    )


def test_assembled_invocation_carries_the_rm_flag(tmp_path: pathlib.Path) -> None:
    """The run invocation always carries --rm — the disposability contract this
    launcher depends on to keep the container itself stateless.
    """
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    result = run_bash(
        tmp_path,
        f"bash {_LAUNCHER} --kanban-root {kanban_root}",
        extra_env=_base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "ARG:--rm" in result.stdout, (
        f"assembled invocation is missing --rm; container state would accumulate "
        f"across launches:\n{result.stdout!r}"
    )


def test_relaunch_against_same_kanban_root_presents_the_same_board(
    tmp_path: pathlib.Path,
) -> None:
    """Two launches against the same kanban root assemble identical mount and env
    flags. This is the behavioral contract disposability depends on: the
    container is disposed of with --rm precisely because all durable state
    lives on the two bind-mounted paths, so relaunching against the same
    kanban root must present the same board both times.
    """
    kanban_root = tmp_path / "root"
    kanban_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _make_fake_engine(fake_bin, "docker")

    env = _base_env(tmp_path, kanban_root=kanban_root, home=home, fake_bin_dir=fake_bin)

    first = run_bash(tmp_path, f"bash {_LAUNCHER} --kanban-root {kanban_root}", extra_env=env)
    second = run_bash(tmp_path, f"bash {_LAUNCHER} --kanban-root {kanban_root}", extra_env=env)

    assert first.returncode == 0, (
        f"first launch: expected exit 0; got {first.returncode}\nstderr: {first.stderr!r}"
    )
    assert second.returncode == 0, (
        f"second launch: expected exit 0; got {second.returncode}\nstderr: {second.stderr!r}"
    )

    first_values = _flag_values(first.stdout, "-v", "-e")
    second_values = _flag_values(second.stdout, "-v", "-e")

    assert first_values, f"no -v/-e values captured from first launch:\n{first.stdout!r}"
    assert first_values == second_values, (
        f"relaunch against the same kanban root produced different mount/env flags:\n"
        f"first:  {first_values!r}\n"
        f"second: {second_values!r}"
    )
