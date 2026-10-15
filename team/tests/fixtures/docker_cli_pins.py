"""
docker_cli_pins.py
===================
Test-side name for the docker/cli-versions.env reader.

The reader itself is :mod:`pgai_agent_kanban.container.cli_pins`, in the
package, so the installed copy of the image-build helper can use it. This
fixture re-exports it unchanged for the build-side tests and cold-build
lanes that import it from here; it holds no parsing logic of its own, so
the file's format is still read in exactly one place.

HOW TO USE
----------
    from tests.fixtures.docker_cli_pins import (
        load_cli_pins,
        cli_pins_build_args,
    )

    pins = load_cli_pins()
    build_args = cli_pins_build_args()

With no argument both resolve docker/cli-versions.env in the repository
this checkout is; see the package module for the refusals and the
``env_path`` override.
"""

from __future__ import annotations

from pgai_agent_kanban.container.cli_pins import (
    REQUIRED_KEYS,
    cli_pins_build_args,
    load_cli_pins,
)

__all__ = ["REQUIRED_KEYS", "cli_pins_build_args", "load_cli_pins"]
