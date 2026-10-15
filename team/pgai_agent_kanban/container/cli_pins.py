"""
cli_pins.py — reader for docker/cli-versions.env, the single source of the
pinned provider-CLI versions every docker/<flavor>/Dockerfile installs.

Every image this repository ships installs two provider CLIs from npm. A
Dockerfile that hardcodes (or leaves unpinned) its own copy of those
versions means the image built today is not the image built yesterday, and
an upstream publish can break a cold build with nothing in the tree having
changed. This module is the one place that turns docker/cli-versions.env
into a form a build invocation can consume, so nothing else needs to know
the file's format. It depends only on the standard library, so it works
wherever the package is installed.

Consumers: :mod:`pgai_agent_kanban.container.image_build` (the argv
docker/build.sh issues), and the build-side test fixtures, which reach it
through ``tests/fixtures/docker_cli_pins.py``.

Usage::

    from pgai_agent_kanban.container.cli_pins import (
        cli_pins_build_args,
        load_cli_pins,
    )

    pins = load_cli_pins()
    # {"CLAUDE_CODE_VERSION": "2.1.268", "CODEX_VERSION": "0.154.0"}

    build_args = cli_pins_build_args()
    # ["--build-arg", "CLAUDE_CODE_VERSION=2.1.268",
    #  "--build-arg", "CODEX_VERSION=0.154.0"]

Path resolution
---------------
With no ``env_path`` argument the file is resolved relative to this
module's own location -- ``<repository root>/docker/cli-versions.env`` when
the module runs from a checkout -- never relative to the caller's working
directory. A caller that knows the repository it builds from (image_build's
``--repo-root``), or a test exercising the parsing against a synthetic
file, passes ``env_path`` explicitly; an installed copy of the package has
no ``docker/`` beside it, so it must.

Refusals
--------
Both failure modes name what a reader needs to fix them:

  * The file is absent -> FileNotFoundError naming the resolved absolute
    path that was not found.
  * The file exists but omits a required key -> ValueError naming the
    specific missing key.

Neither path substitutes a default version. A silently-defaulted CLI
version is exactly the unpinned-build problem this file exists to close.
"""

from __future__ import annotations

import pathlib

__all__ = ["REQUIRED_KEYS", "cli_pins_build_args", "load_cli_pins"]

# Repository root when run from a checkout: four levels up from this file
# (container/ -> pgai_agent_kanban/ -> team/ -> repository root).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

_DEFAULT_ENV_PATH = _REPO_ROOT / "docker" / "cli-versions.env"

# The complete set of keys docker/cli-versions.env must define. Order here
# is the order build args are emitted in.
REQUIRED_KEYS: tuple[str, ...] = ("CLAUDE_CODE_VERSION", "CODEX_VERSION")


def _parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Parse a KEY=value-per-line env file into a mapping.

    Blank lines and lines beginning with ``#`` are skipped. Every other
    line is split at its first ``=`` into a key and a value; surrounding
    whitespace on either side is stripped.
    """
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        pins[key.strip()] = value.strip()
    return pins


def load_cli_pins(env_path: pathlib.Path | str | None = None) -> dict[str, str]:
    """Return the pinned CLI versions as a ``{KEY: version}`` mapping.

    Args:
        env_path: The env file to read. Defaults to docker/cli-versions.env
            resolved against this module's own location (see the module
            docstring); pass it whenever the repository root is known.

    Returns:
        A dict containing exactly the keys in REQUIRED_KEYS, mapped to the
        version string each is pinned to.

    Raises:
        FileNotFoundError: the resolved path does not exist. The message
            names the resolved absolute path.
        ValueError: the file exists but does not define one of the
            required keys. The message names the specific missing key.
    """
    path = pathlib.Path(env_path) if env_path is not None else _DEFAULT_ENV_PATH
    if not path.is_file():
        raise FileNotFoundError(f"CLI versions file not found: {path}")

    parsed = _parse_env_file(path)

    for key in REQUIRED_KEYS:
        if key not in parsed:
            raise ValueError(f"{path} does not define required key: {key}")

    return {key: parsed[key] for key in REQUIRED_KEYS}


def cli_pins_build_args(env_path: pathlib.Path | str | None = None) -> list[str]:
    """Return the pinned CLI versions as a flat container-build argv slice.

    The result alternates ``--build-arg`` with a ``KEY=value`` entry for
    each key in REQUIRED_KEYS, in order, so it can be spliced directly into
    a container-engine build command::

        [engine, "build", *cli_pins_build_args(env_path), ...]

    Args:
        env_path: Same as load_cli_pins.

    Raises:
        Same as load_cli_pins -- this function parses the same file.
    """
    pins = load_cli_pins(env_path)
    build_args: list[str] = []
    for key in REQUIRED_KEYS:
        build_args.extend(["--build-arg", f"{key}={pins[key]}"])
    return build_args
