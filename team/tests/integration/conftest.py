"""
conftest.py — integration-scoped pytest configuration and fixture re-exports.

This conftest covers tests under team/tests/integration/ only.  It does NOT
duplicate logic from team/tests/conftest.py (the parent conftest), which
already provides the autouse safety net for every test in the suite:

  - _block_live_kanban_writes   — redirects all kanban env vars to a safe temp root
  - _sandbox_crontab            — stubs the crontab binary so tests never touch the
                                  real /var/spool/cron
  - tmp_kanban_root             — a synthetic kanban tree for light queue/state tests
  - pgai_tests_temp_dir         — base temp directory respecting PGAI_AGENT_KANBAN_TEMP_DIR
  - pgai_mkdtemp()              — creates temp dirs under the framework root

Integration tests inherit all of the above automatically through pytest's conftest.py
hierarchy.  This file adds only what is specific to the integration scope:

  Re-exported fixtures (imported so integration test files can request them by
  name without importing from the fixture module directly):

  - installed_root    — simulated installed-tree layout (no team/ shim); from
                        tests/fixtures/installed_root.py.  Integration tests use
                        this to exercise real code paths against an installed-
                        layout kanban root rather than the dev tree.

  - two_project_root  — kanban root with project_a + project_b registered; from
                        tests/fixtures/two_project.py.  Integration tests use
                        this to exercise multi-project isolation: real discovery,
                        real RC lifecycle, real operator commands across projects.

  - api_server        — teardown-guaranteed API server on an ephemeral port;
                        from tests/fixtures/api_server.py.  Starts a real
                        uvicorn subprocess, yields a ServerHandle(port, pid),
                        and stops it in a finally block, asserting the port is
                        free before returning.  Never binds port 8300.

Integration test contract (from SOP.md):
  - Integration tests do NOT mock the integration points.  They stand up real
    temporary kanban trees (via the fixtures above) and exercise actual flows
    (wake-script behavior, discovery, the RC lifecycle, operator commands) end
    to end.  This is where "units pass but real flows break" bugs are caught.
  - All scratch contained under pytest's tmp_path or the framework temp root
    (PGAI_AGENT_KANBAN_TEMP_DIR).  No bare /tmp paths.  No HOME paths.
  - Test names describe behavior, never bug IDs, version numbers, or scaffolding
    labels (SOP.md Anti-pattern 6).
  - No order dependence across tests (passes under pytest-randomly).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-export fixtures from team/tests/fixtures/ so integration test authors can
# request them by name without importing from the fixture module directly.
# ---------------------------------------------------------------------------

# Importing the fixture functions into this conftest's namespace makes pytest
# discover and register them for all tests under tests/integration/.
from tests.fixtures.installed_root import installed_root  # noqa: F401
from tests.fixtures.two_project import two_project_root  # noqa: F401
from tests.fixtures.api_server import api_server  # noqa: F401
