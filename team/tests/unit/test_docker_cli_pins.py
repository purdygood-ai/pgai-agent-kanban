"""
test_docker_cli_pins.py
========================
Behavioral unit tests for pgai_agent_kanban.container.cli_pins, the reader
that turns docker/cli-versions.env into the pinned CLI version mapping and
the flat --build-arg list a container-engine build invocation consumes.

Covers five behaviors:
  1. Both required keys parse correctly from a well-formed env file.
  2. The build-arg flag list has the exact alternating shape
     ['--build-arg', 'KEY=value', '--build-arg', 'KEY=value', ...].
  3. An absent env file raises FileNotFoundError naming the resolved path.
  4. An env file missing a required key raises ValueError naming that key.
  5. tests/fixtures/docker_cli_pins.py hands out the package's own reader,
     not a second parser of the file.
  6. The shipped docker/cli-versions.env, read through the same reader with
     no override, pins the provider CLI versions the template's default
     models need.

Every other scenario passes an explicit env_path override so it neither
depends on nor mutates the real docker/cli-versions.env.

Test naming (roles/TESTER.md anti-pattern 6):
Names describe behavior, never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.container import cli_pins
from pgai_agent_kanban.container.cli_pins import cli_pins_build_args, load_cli_pins
from tests.fixtures import docker_cli_pins


def _write_env_file(path: pathlib.Path, content: str) -> pathlib.Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_cli_pins_parses_both_required_keys(tmp_path: pathlib.Path) -> None:
    env_file = _write_env_file(
        tmp_path / "cli-versions.env",
        "# header comment, ignored\n"
        "\n"
        "CLAUDE_CODE_VERSION=2.1.268\n"
        "CODEX_VERSION=0.154.0\n",
    )

    pins = load_cli_pins(env_file)

    assert pins == {
        "CLAUDE_CODE_VERSION": "2.1.268",
        "CODEX_VERSION": "0.154.0",
    }


def test_cli_pins_build_args_shape_alternates_flag_and_key_value(
    tmp_path: pathlib.Path,
) -> None:
    env_file = _write_env_file(
        tmp_path / "cli-versions.env",
        "CLAUDE_CODE_VERSION=2.1.268\nCODEX_VERSION=0.154.0\n",
    )

    build_args = cli_pins_build_args(env_file)

    assert build_args == [
        "--build-arg",
        "CLAUDE_CODE_VERSION=2.1.268",
        "--build-arg",
        "CODEX_VERSION=0.154.0",
    ]


def test_load_cli_pins_refuses_absent_file_naming_resolved_path(
    tmp_path: pathlib.Path,
) -> None:
    missing_file = tmp_path / "does-not-exist" / "cli-versions.env"

    with pytest.raises(FileNotFoundError) as exc_info:
        load_cli_pins(missing_file)

    assert str(missing_file) in str(exc_info.value)


def test_load_cli_pins_refuses_file_missing_required_key(
    tmp_path: pathlib.Path,
) -> None:
    env_file = _write_env_file(
        tmp_path / "cli-versions.env",
        # CODEX_VERSION is absent -- no default may be substituted.
        "CLAUDE_CODE_VERSION=2.1.268\n",
    )

    with pytest.raises(ValueError) as exc_info:
        load_cli_pins(env_file)

    assert "CODEX_VERSION" in str(exc_info.value)


def test_fixture_reexports_the_package_reader() -> None:
    assert docker_cli_pins.load_cli_pins is cli_pins.load_cli_pins
    assert docker_cli_pins.cli_pins_build_args is cli_pins.cli_pins_build_args
    assert docker_cli_pins.REQUIRED_KEYS is cli_pins.REQUIRED_KEYS


def test_shipped_pins_name_the_current_provider_cli_versions() -> None:
    assert load_cli_pins() == {
        "CLAUDE_CODE_VERSION": "2.1.296",
        "CODEX_VERSION": "0.162.1",
    }
