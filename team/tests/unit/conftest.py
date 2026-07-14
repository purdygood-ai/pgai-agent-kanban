"""
conftest.py — unit-scoped pytest configuration and fixture re-exports.

This conftest covers tests under team/tests/unit/ only.  It does NOT
duplicate logic from team/tests/conftest.py (the parent conftest), which
already provides the autouse safety net for every test in the suite:

  - _block_live_kanban_writes   — redirects all kanban env vars to a safe temp root
  - _sandbox_crontab            — stubs the crontab binary so tests never touch the
                                  real /var/spool/cron
  - tmp_kanban_root             — a synthetic kanban tree for light queue/state tests
  - pgai_tests_temp_dir         — base temp directory respecting PGAI_AGENT_KANBAN_TEMP_DIR
  - pgai_mkdtemp()              — creates temp dirs under the framework root

Unit tests inherit all of the above automatically through pytest's conftest.py
hierarchy.  This file adds only what is specific to the unit scope:

  Re-exported fixtures (imported so unit test files can request without explicit
  fixture-module imports):

  - installed_root    — simulated installed-tree layout (no team/ shim); from
                        tests/fixtures/installed_root.py.  Closes the
                        test-fidelity gap where tests ran in the dev tree where
                        the Python shim package is always importable.

  - two_project_root  — kanban root with project_a + project_b registered; from
                        tests/fixtures/two_project.py.  Closes the gap where
                        single-project fixtures cannot exercise cross-project
                        isolation guarantees.

  - log_stub_fragment — callable factory returning a Bash log() stub fragment;
                        from tests/fixtures/log_stub.py.

  - log_stub_capture  — LogStubCapture context manager fixture; from
                        tests/fixtures/log_stub.py.

  - api_server        — teardown-guaranteed API server on an ephemeral port;
                        from tests/fixtures/api_server.py.  Starts a real
                        uvicorn subprocess, yields a ServerHandle(port, pid),
                        and stops it in a finally block, asserting the port is
                        free before returning.  Never binds port 8300.

  Unit-specific fixtures:

  - minimal_kanban_root — a leaner kanban tree than tmp_kanban_root, suitable
                          for pure-logic unit tests that need a root directory
                          and a release-state.md but do not need queue files or
                          workflow YAML.  Avoids the overhead of building a full
                          multi-project tree for tests that just need a root path.

Unit test naming contract:
  - Test function and file names describe behavior under test, never bug IDs,
    version numbers, or scaffolding labels (SOP.md Anti-pattern 6).
  - All temp paths use pytest's tmp_path fixture (propagated from the parent
    conftest's PYTEST_DEBUG_TEMPROOT redirect).
  - No bare /tmp paths anywhere in test or fixture code.
"""

from __future__ import annotations

import pathlib

import pytest

# ---------------------------------------------------------------------------
# Re-export fixtures from team/tests/fixtures/ so unit test authors can
# request them by name without importing from the fixture module directly.
# ---------------------------------------------------------------------------

# Importing the fixture functions into this conftest's namespace makes pytest
# discover and register them for all tests under tests/unit/.
from tests.fixtures.installed_root import installed_root  # noqa: F401
from tests.fixtures.two_project import two_project_root  # noqa: F401
from tests.fixtures.log_stub import log_stub_fragment  # noqa: F401
from tests.fixtures.log_stub import log_stub_capture  # noqa: F401
from tests.fixtures.api_server import api_server  # noqa: F401


# ---------------------------------------------------------------------------
# Unit-specific fixture: minimal_kanban_root
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal kanban root directory for pure-logic unit tests.

    Lighter than tmp_kanban_root (which builds queue files for every agent)
    and lighter than two_project_root (which builds a full two-project tree).
    Use this when a test only needs a root path and a release-state.md — for
    example, when testing a function that reads the Active RC field.

    Layout created:
        tmp_path/
            release-state.md    — Active RC: none (idle)

    The directory is automatically cleaned up by pytest when the test finishes
    (honoring the retention policy in pyproject.toml).

    Returns:
        pathlib.Path — the kanban root (i.e. tmp_path itself).
    """
    root = tmp_path

    (root / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## State\n"
        "IDLE\n\n"
        "## Notes\n"
        "Synthetic root created by minimal_kanban_root fixture.\n",
        encoding="utf-8",
    )

    return root
