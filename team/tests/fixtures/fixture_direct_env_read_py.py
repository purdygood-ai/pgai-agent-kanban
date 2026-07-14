"""fixture_direct_env_read_py.py
=================================
DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL CODE.

This module supplies a scratch-negative Python fixture for
test_lint_env_bootstrap.py.  It describes a Python entry point that reads
PGAI_AGENT_KANBAN_ROOT_PATH directly via os.environ instead of importing
resolve_kanban_root from pgai_agent_kanban.env.

This is exactly the violation that lint_env_bootstrap.py's Python-side check
must flag.  The test writes this content to a temporary file and asserts that
the lint returns a non-empty violations list.

This module is intentionally kept in tests/fixtures/ so it is excluded from
the real Python-side scan (which only scans scripts/*.py and
pgai_agent_kanban/**/*.py, not tests/).
"""

# Content of a Python file that violates the Python-side env-bootstrap contract.
# The file reads PGAI_AGENT_KANBAN_ROOT_PATH directly via os.environ instead of
# calling resolve_kanban_root() from the canonical resolver module.
PYTHON_VIOLATION_CONTENT = """\
#!/usr/bin/env python3
\"\"\"Deliberate violation fixture: Python entry point that bypasses the
canonical resolver and reads PGAI_AGENT_KANBAN_ROOT_PATH directly.

lint_env_bootstrap.py must flag this file on the Python-side check.
\"\"\"

import os
import pathlib

# Violation: direct os.environ read instead of resolve_kanban_root()
root_raw = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "")
if not root_raw:
    raise RuntimeError("PGAI_AGENT_KANBAN_ROOT_PATH is not set")

KANBAN_ROOT = pathlib.Path(root_raw).resolve()
"""
