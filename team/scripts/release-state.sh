#!/usr/bin/env bash
# team/scripts/release-state.sh
# Read-only operator verb: print a project's release-state.md fields.
#
# Usage:
#   release-state.sh --project <name> [--help]
#
# Read-only twin of GET /release-state (team/pgai_agent_kanban/api/reads.py)
# -- both surfaces resolve through the exact same function,
# pgai_agent_kanban.ops.read.get_release_state, which itself delegates to
# the shared, schema-driven validating loader
# (pgai_agent_kanban.cm.read_state_field.load_release_state). Neither
# surface reads release-state.md's raw text or re-derives the field
# vocabulary independently -- "one shared resolver, two renderers" (this
# ticket's own architecture constraint).
#
# release-state.md is CM's file: this script only ever reads it, never
# writes it (team/SOP-core.md "Release State Fields").
#
# Output (stdout), one "key=value" line per field, in schema order:
#   active_rc=<vX.Y.Z|none>
#   rc_opened_at=<ISO-8601|none>
#   rc_opened_by_task=<task id|none>
#   last_released=<vX.Y.Z|none>
#
# A field legitimately unset (e.g. a project that has never opened an RC)
# prints the literal value "none" -- a validated value, not an absence.
#
# Exit codes:
#   0   Fields printed to stdout.
#   1   Usage error (undeclared flag, missing --project).
#   2   Project not found.
#   3   release-state.md failed schema validation -- the dual-channel
#       error-contract block (error/message/remedy) is printed to stderr
#       rather than a guessed value being printed to stdout. The three
#       fields are rendered by pgai_agent_kanban.lib.error_contract's
#       dual_channel_from_text, the same function GET /release-state builds
#       its JSON refusal body from, fed the same str(exc) and the same
#       fallback key/remedy (pgai_agent_kanban.ops.read), so the CLI's
#       remedy: line and the REST twin's "remedy" value are one string.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_RS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_RS_LIB_DIR="${_RS_SCRIPT_DIR}/lib"

# shellcheck source=lib/operator_args.sh
source "${_RS_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_RS_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_RS_LIB_DIR}/pp_run_ops.sh"

OPERATOR_VALID_FLAGS=(project help)

operator_args_parse "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "release-state.sh" \
        "Print a project's release-state.md fields (read-only)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Prints the four validated fields -- active_rc, rc_opened_at," \
        "rc_opened_by_task, last_released -- one 'key=value' line each," \
        "resolved through the SAME validating loader GET /release-state" \
        "calls (never a second, independent parse of release-state.md)." \
        "" \
        "Exit codes:" \
        "  0  Fields printed" \
        "  1  Usage error" \
        "  2  Project not found" \
        "  3  release-state.md failed schema validation (dual-channel" \
        "     error/message/remedy block printed to stderr)"
    exit 0
fi

operator_args_validate_known "release-state.sh" OPERATOR_VALID_FLAGS || exit 1

_rs_project="$(operator_args_project)"
if [[ -z "${_rs_project}" ]]; then
    printf 'release-state.sh: --project is required\n' >&2
    exit 1
fi

_rs_project="$(pp_resolve_project_name "${_rs_project}")" || exit 1

PYTHONPATH="$(pp_pythonpath)" python3 - "${PGAI_AGENT_KANBAN_ROOT_PATH}" "${_rs_project}" <<'PY'
import sys

from pgai_agent_kanban.lib.error_contract import (
    dual_channel_from_text,
    format_error_contract,
)
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import NotFound, Refused
from pgai_agent_kanban.ops.read import (
    RELEASE_STATE_REFUSAL_ERROR_KEY,
    RELEASE_STATE_REFUSAL_FALLBACK_REMEDY,
    get_release_state,
)

kanban_root, project = sys.argv[1], sys.argv[2]
ctx = OpsContext(kanban_root=kanban_root)

try:
    fields = get_release_state(ctx, project)
except NotFound as exc:
    print(f"release-state.sh: {exc}", file=sys.stderr)
    sys.exit(2)
except Refused as exc:
    # One renderer for both surfaces: the same str(exc), the same shared
    # key/remedy fallback, through the same function GET /release-state
    # uses -- so the remedy: line below is byte-equal to the REST twin's
    # "remedy" value, whether the raiser produced the house shape or not.
    body = dual_channel_from_text(
        str(exc),
        RELEASE_STATE_REFUSAL_ERROR_KEY,
        RELEASE_STATE_REFUSAL_FALLBACK_REMEDY,
    )
    print(format_error_contract(body["error"], body["message"], body["remedy"]), file=sys.stderr)
    sys.exit(3)

# Snake-case the schema's Title Case headings for a stable, greppable
# key=value shape -- the JSON REST twin uses the same four names.
print(f"active_rc={fields['Active RC']}")
print(f"rc_opened_at={fields['RC Opened At']}")
print(f"rc_opened_by_task={fields['RC Opened By Task']}")
print(f"last_released={fields['Last Released']}")
PY
