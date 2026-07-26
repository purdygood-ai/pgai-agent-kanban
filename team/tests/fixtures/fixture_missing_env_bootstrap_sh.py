"""fixture_missing_env_bootstrap_sh.py
======================================
DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL SCRIPTS.

This module supplies scratch-negative bash fixtures for
test_lint_env_bootstrap.py.  Two violation patterns are captured here:

1. BASH_VIOLATION_CONTENT — a script that references PGAI_AGENT_KANBAN_ROOT_PATH
   without sourcing ANY approved prelude at all (absence violation).

2. BASH_ORDERING_VIOLATION_CONTENT — a script that DOES source env_bootstrap.sh
   but only AFTER the first runtime use of PGAI_AGENT_KANBAN_ROOT_PATH (ordering
   violation).  This is the predicate hole identified by BUG-0073: the old
   presence-only check passed this script even though it fails from a fresh shell.
   The closed-hole predicate (``_source_precedes_first_usage``) must flag it.

These fixtures are intentionally kept out of the normal scripts/ tree so they
cannot pollute the real codebase.  lint_env_bootstrap.py excludes the
tests/fixtures/ directory from its bash-side scan by only examining
scripts/, scripts/cm/, and scripts/dashboard/.
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

# Content of a shell script that has env_bootstrap.sh sourced, but AFTER the
# first runtime use of PGAI_AGENT_KANBAN_ROOT_PATH.  This is the ordering
# violation pattern that BUG-0073 found in po-agent.sh (and eight other scripts).
#
# The old presence-only predicate in lint_env_bootstrap.py passed this file
# because it only checked whether the source call existed anywhere in the file.
# The closed-hole predicate (``_source_precedes_first_usage``) must flag it
# because the env var is used at line 6 while the bootstrap runs at line 12.
#
# This fixture is kept in the test suite as the closed-hole proof: if a future
# change breaks the ordering check, this fixture test will catch the regression.
BASH_ORDERING_VIOLATION_CONTENT = """\
#!/usr/bin/env bash
# Deliberate ordering-violation fixture: env_bootstrap.sh is sourced AFTER the
# first use of PGAI_AGENT_KANBAN_ROOT_PATH.  This script would fail from a fresh
# shell even though the old lint predicate reported it as clean.
#
# lint_env_bootstrap.py's ordering check must flag this file (BUG-0073 closed).
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ ! -d "$TEAM_ROOT" ]]; then
  echo "ERROR: kanban root not found: $TEAM_ROOT" >&2
  exit 1
fi
set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"
echo "root is: ${TEAM_ROOT}"
"""
