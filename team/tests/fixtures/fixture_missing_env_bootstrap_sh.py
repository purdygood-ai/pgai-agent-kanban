"""fixture_missing_env_bootstrap_sh.py
======================================
DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL SCRIPTS.

This module supplies a scratch-negative bash fixture for
test_lint_env_bootstrap.py.  It describes an entry-point shell script that
references PGAI_AGENT_KANBAN_ROOT_PATH on a non-comment line but does NOT
source env_bootstrap.sh or wake_common.sh.

This is exactly the violation that lint_env_bootstrap.py's bash-side check
must flag.  The test writes this content to a temporary file and asserts that
the lint returns a non-empty violations list.

This module is intentionally kept out of the normal scripts/ tree so the
violation fixture cannot pollute the real codebase.  lint_env_bootstrap.py
excludes the tests/fixtures/ directory from its bash-side scan by only
examining scripts/, scripts/cm/, and scripts/dashboard/.
"""

# Content of a shell script that violates the bash-side env-bootstrap contract.
# The script uses PGAI_AGENT_KANBAN_ROOT_PATH directly without sourcing either
# approved prelude (env_bootstrap.sh or wake_common.sh).
BASH_VIOLATION_CONTENT = """\
#!/usr/bin/env bash
# Deliberate violation fixture: entry point that reads PGAI_AGENT_KANBAN_ROOT_PATH
# without sourcing env_bootstrap.sh or wake_common.sh.
#
# lint_env_bootstrap.py must flag this file on the bash-side check.
set -euo pipefail

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
echo "root is: ${KANBAN_ROOT}"
"""
