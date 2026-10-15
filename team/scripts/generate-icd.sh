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
# Baseline policy (canonical regenerations only -- never with --output PATH):
# the per-version baseline docs/api/baselines/icd-v<ICD_VERSION>.json is
# created only when ICD_VERSION is newer (semver) than every existing
# baseline, refreshed only while the version is not yet listed in
# docs/api/baselines/SUPPORTED, and NEVER rewritten once it is listed there.
# A regeneration that changes icd.json at a published version writes
# icd.json, leaves the published baseline byte-untouched, prints
# error / message / remedy naming the baseline file and the ICD_VERSION
# bump step, and exits 1.  Freezing is not unconditional; adding a version
# to SUPPORTED remains a separate operator decision.
#
# Usage:
#   generate-icd.sh [--output PATH] [--refreeze]
#
# Options:
#   --output PATH   Write ICD JSON to PATH instead of docs/api/icd.json.
#                   A non-default PATH never touches any baseline file.
#   --refreeze      Rewrite the PUBLISHED baseline icd-v<ICD_VERSION>.json
#                   (a version listed in SUPPORTED) to the new artifact
#                   instead of refusing.  Explicit override only; the default
#                   never re-freezes a published baseline, and no other
#                   version's baseline is ever touched.
#   --help, -h      Show this help and exit.
#
# Exit codes:
#   0   ICD artifact written successfully; baseline policy applied.
#   1   Error (Python not found, ICD_VERSION missing, output unwritable,
#       etc.), or the published-baseline refusal (icd-v<ICD_VERSION>.json is
#       listed in SUPPORTED, differs from the new artifact, and --refreeze
#       was not given).
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
# --output PATH, --refreeze or --help directly to generate_icd.py.
exec python3 -m pgai_agent_kanban.api.generate_icd "$@"
