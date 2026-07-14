#!/usr/bin/env bash
# generate-icd.sh
# Thin wrapper around team/pgai_agent_kanban/api/generate_icd.py.
#
# Builds the FastAPI app in-process (no running server, no network calls),
# calls app.openapi(), stamps info.version from docs/api/ICD_VERSION, and
# writes deterministic JSON (sorted keys, fixed indent, trailing newline)
# to docs/api/icd.json.
#
# Two runs on the same tree produce byte-identical output.
#
# Usage:
#   generate-icd.sh [--output PATH]
#
# Options:
#   --output PATH   Write ICD JSON to PATH instead of docs/api/icd.json.
#   --help, -h      Show this help and exit.
#
# Exit codes:
#   0   ICD artifact written successfully.
#   1   Error (Python not found, ICD_VERSION missing, output unwritable, etc.).
#
# The script resolves the repository root from its own location so it can be
# invoked from any working directory.

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REPO_ROOT="$(cd "${_SCRIPT_DIR}/../.." && pwd)"
_TEAM_DIR="${_REPO_ROOT}/team"

# Check Python 3 is available.
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found in PATH." >&2
    exit 1
fi

# Ensure the pgai_agent_kanban package is importable regardless of the
# caller's working directory.  team/ contains the package root, so prepend
# it to PYTHONPATH before handing off to the module.  The caller's CWD is
# preserved so that relative --output paths resolve against the caller's
# location, not team/.
export PYTHONPATH="${_TEAM_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

# Run the generator module.  Forward all arguments so the caller can pass
# --output PATH or --help directly to generate_icd.py.
exec python3 -m pgai_agent_kanban.api.generate_icd "$@"
