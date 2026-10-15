"""
conftest.py — pytest configuration for the operator API test suite.

Two jobs:

1. Adds the project root (the parent of team/) to sys.path so that app.py's
   ``from team.pgai_agent_kanban...`` imports resolve correctly when the test
   suite is invoked from inside team/ (the normal pytest invocation root).

2. Registers the framework-wide safety-net fixtures from team/tests/conftest.py
   for every test in this directory.  pytest applies a conftest.py only to
   the directory it lives in and the directories below it, and this package
   (team/pgai_agent_kanban/api/tests/) is not below team/tests/, so the
   autouse fixtures declared there -- ``_block_live_kanban_writes``, which
   redirects every kanban root name to a per-test temp root,
   ``_sandbox_crontab``, which shadows the crontab binary, and
   ``_loopback_test_client_host``, which points every TestClient at a
   loopback base_url so the API's Host allow-list admits it -- do not reach
   these tests on their own.  Importing the fixture functions into this
   module's namespace registers them here, the same re-export idiom
   team/tests/integration/conftest.py uses, so a test in this package that
   builds an app or runs a script without pinning its own root still lands
   on a temp root and never on the operator's install.
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

# team/ itself must be importable for the ``tests`` package below; pytest
# puts it there when it loads this conftest, and an operator running pytest
# from another directory gets the same guarantee here.
_team_dir_str = str(_TEAM_DIR)
if _team_dir_str not in sys.path:
    sys.path.insert(0, _team_dir_str)

# Autouse safety net (see the module docstring): importing the fixture
# functions registers them for every test under this directory.  Only the
# fixtures are imported -- never the ``pytest_*`` hooks that module also
# defines, which pytest has already registered once from the conftest itself.
from tests.conftest import _block_live_kanban_writes  # noqa: E402,F401
from tests.conftest import _sandbox_crontab  # noqa: E402,F401
from tests.conftest import _loopback_test_client_host  # noqa: E402,F401
