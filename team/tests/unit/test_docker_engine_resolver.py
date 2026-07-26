"""
test_docker_engine_resolver.py
===============================
Behavioral unit tests for team/scripts/lib/docker_engine.sh (resolve_docker_exe).

Covers the container-engine resolution contract used by the container-shell
launcher:

  1. Absent kanban.cfg / absent [docker] section / absent docker_exe key ->
     resolves to "docker" (no error) as long as a "docker" binary is on PATH.
  2. A configured docker_exe value that resolves on PATH is used as-is.
  3. A configured docker_exe value that does not resolve on PATH is a hard
     failure: non-zero exit, empty stdout, and a stderr message naming both
     the docker_exe key and the offending value.
  4. A blank docker_exe value (key present, empty value) falls back to the
     "docker" default, same as an absent key.
  5. Calling the resolver without a kanban_root argument is a usage error.

Tests source docker_engine.sh (and its ini_parser.sh dependency) and invoke
resolve_docker_exe via the shared bash test harness. A per-test fake-binary
directory is prepended to PATH so the tests never depend on a real docker or
podman installation being present on the host running the suite.
"""

from __future__ import annotations

import os
import pathlib
import stat

from tests.unit.shell_harness import run_bash

_INI_PARSER = "scripts/lib/ini_parser.sh"
_DOCKER_ENGINE = "scripts/lib/docker_engine.sh"


def _make_fake_binary(bin_dir: pathlib.Path, name: str) -> None:
    """Create an executable stub named *name* under *bin_dir*."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\necho fake-{name}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _resolve(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    *,
    fake_bin_dir: pathlib.Path | None = None,
):
    """Source the resolver and call resolve_docker_exe against kanban_root."""
    extra_env = dict(os.environ)
    if fake_bin_dir is not None:
        extra_env["PATH"] = f"{fake_bin_dir}:{extra_env.get('PATH', '')}"
    script = (
        f"source {_INI_PARSER} && source {_DOCKER_ENGINE} && "
        f"resolve_docker_exe '{kanban_root}'"
    )
    return run_bash(tmp_path, script, extra_env=extra_env)


# ---------------------------------------------------------------------------
# 1. Absent kanban.cfg -> "docker" default
# ---------------------------------------------------------------------------


def test_absent_kanban_cfg_resolves_to_docker(tmp_path: pathlib.Path) -> None:
    """No kanban.cfg on disk resolves to the "docker" default when it is on PATH."""
    kanban_root = tmp_path / "root_absent"
    kanban_root.mkdir()
    fake_bin = tmp_path / "fakebin_absent"
    _make_fake_binary(fake_bin, "docker")

    result = _resolve(tmp_path, kanban_root, fake_bin_dir=fake_bin)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "docker"


def test_absent_docker_section_resolves_to_docker(tmp_path: pathlib.Path) -> None:
    """kanban.cfg with no [docker] section at all resolves to "docker"."""
    kanban_root = tmp_path / "root_no_section"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text(
        "[providers]\nactive = claude\n", encoding="utf-8"
    )
    fake_bin = tmp_path / "fakebin_no_section"
    _make_fake_binary(fake_bin, "docker")

    result = _resolve(tmp_path, kanban_root, fake_bin_dir=fake_bin)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "docker"


# ---------------------------------------------------------------------------
# 2. Configured value present and valid -> used as-is
# ---------------------------------------------------------------------------


def test_configured_podman_used_when_present_on_path(tmp_path: pathlib.Path) -> None:
    """A configured docker_exe = podman is returned when podman resolves on PATH."""
    kanban_root = tmp_path / "root_podman"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text(
        "[docker]\ndocker_exe = podman\n", encoding="utf-8"
    )
    fake_bin = tmp_path / "fakebin_podman"
    _make_fake_binary(fake_bin, "podman")

    result = _resolve(tmp_path, kanban_root, fake_bin_dir=fake_bin)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "podman"


# ---------------------------------------------------------------------------
# 3. Configured value invalid/missing -> fail loud naming key and value
# ---------------------------------------------------------------------------


def test_configured_invalid_binary_fails_loud(tmp_path: pathlib.Path) -> None:
    """A docker_exe value with no matching PATH entry fails with a named error."""
    kanban_root = tmp_path / "root_invalid"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text(
        "[docker]\ndocker_exe = nonexistent-container-engine-xyz\n", encoding="utf-8"
    )
    # No fake binary directory: the configured name must not resolve.
    empty_bin = tmp_path / "fakebin_invalid_empty"
    empty_bin.mkdir()

    result = _resolve(tmp_path, kanban_root, fake_bin_dir=empty_bin)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "docker_exe" in result.stderr
    assert "nonexistent-container-engine-xyz" in result.stderr


# ---------------------------------------------------------------------------
# 4. Blank configured value -> falls back to "docker" default
# ---------------------------------------------------------------------------


def test_blank_docker_exe_value_falls_back_to_docker(tmp_path: pathlib.Path) -> None:
    """docker_exe present but blank behaves the same as an absent key."""
    kanban_root = tmp_path / "root_blank"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text(
        "[docker]\ndocker_exe = \n", encoding="utf-8"
    )
    fake_bin = tmp_path / "fakebin_blank"
    _make_fake_binary(fake_bin, "docker")

    result = _resolve(tmp_path, kanban_root, fake_bin_dir=fake_bin)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "docker"


# ---------------------------------------------------------------------------
# 5. Missing kanban_root argument -> usage error
# ---------------------------------------------------------------------------


def test_missing_kanban_root_argument_is_a_usage_error(tmp_path: pathlib.Path) -> None:
    """Calling resolve_docker_exe with no argument is a usage error, not a silent default."""
    script = f"source {_INI_PARSER} && source {_DOCKER_ENGINE} && resolve_docker_exe"
    result = run_bash(tmp_path, script)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "kanban_root" in result.stderr
