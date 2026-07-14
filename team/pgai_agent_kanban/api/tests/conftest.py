"""
conftest.py — pytest configuration for the operator API test suite.

Adds the project root (the parent of team/) to sys.path so that app.py's
``from team.pgai_agent_kanban...`` imports resolve correctly when the test
suite is invoked from inside team/ (the normal pytest invocation root).

This file is intentionally minimal: the main test-harness conftest.py at
team/tests/conftest.py already provides the framework-wide safety fixtures
(_block_live_kanban_writes, _sandbox_crontab, etc.).  That parent conftest
is automatically picked up by pytest for all tests under team/ including
this subdirectory.
"""

import pathlib
import sys

# This file lives at team/pgai_agent_kanban/api/tests/conftest.py.
# Going up four levels reaches the project root (parent of team/).
_API_TESTS_DIR = pathlib.Path(__file__).parent
_TEAM_DIR = _API_TESTS_DIR.parent.parent.parent  # team/
_PROJECT_ROOT = _TEAM_DIR.parent                  # project root (contains team/)

# Add the project root to sys.path so that app.py can resolve its
# ``from team.pgai_agent_kanban...`` imports when tests run from team/.
_project_root_str = str(_PROJECT_ROOT)
if _project_root_str not in sys.path:
    sys.path.insert(0, _project_root_str)
