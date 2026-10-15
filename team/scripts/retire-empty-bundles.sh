#!/usr/bin/env bash
# team/scripts/retire-empty-bundles.sh
# Operator script: reports, and optionally retires, requirements bundles
# whose ## Bundled Items resolve to no real item file.
#
# A discovery-generated bundle (*-bugfix-bundle-*.md, *-priority-bundle-*.md)
# can end up carrying only narration lines under ## Bundled Items -- log
# lines the pipeline recorded rather than an actual bug or priority file.
# Such a bundle still consumes a version slot every time it reaches the PM
# step, and left alone it is decomposed into an empty release. This script
# finds that class of bundle and gives an operator a safe way to close it
# out without hand-editing files.
#
# Usage:
#   retire-empty-bundles.sh --project <name> [--apply] [--help]
#
# Options:
#   --project NAME   project name; must match a directory under
#                    projects/<name>/ and be registered in projects.cfg
#   --apply          write ## Status: done to every retirable bundle found.
#                    Without this flag the script only reports; it is the
#                    default and writes nothing to disk.
#   --help, -h       print this help and exit 0
#
# Eligibility rule:
#   A bundle is retirable only when its ## Status is not already "done" or
#   "running", AND every line under its ## Bundled Items section fails to
#   resolve to an existing file on disk (including a bundle with zero item
#   lines at all). A bundle with at least one item line that resolves to a
#   real file is reported as kept and is never modified, in either mode.
#   A bundle already at ## Status done or running is reported as skipped
#   and is never modified, in either mode.
#
# Modes:
#   Report mode (default, no --apply): prints one stanza per bundle file
#   considered with its verdict (retirable / kept / skipped) and reason,
#   then a summary line. Modifies nothing -- verifiable by comparing file
#   checksums before and after the run.
#
#   Apply mode (--apply): performs the same scan, then sets ## Status to
#   done on exactly the bundles classified retirable. Every other header
#   line in every file -- retirable, kept, or skipped -- is left
#   byte-identical; a requirements file is never deleted.
#
# Exit codes:
#   0   scan (and, with --apply, retirement) completed; see output for
#       what was found and done -- finding zero retirable bundles is not
#       an error.
#   1   argument error, or the project/requirements directory could not
#       be resolved.

# ---------------------------------------------------------------------------
# Argument parsing
# Done before strict mode so error messages print cleanly on bad flags.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh (canonical flag parsing and --help rendering).
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Declared flag vocabulary: drives --help output; flags the command accepts.
OPERATOR_VALID_FLAGS=(project apply help)

# Parse: operator_args_parse normalizes -h to --help before delegation.
# Value-taking: project. Boolean: apply, help.
operator_args_parse "$@"

# Handle --help / -h.
if argparse_has "help"; then
    operator_args_render_help_for_flags "retire-empty-bundles.sh" \
        "Report, and optionally retire, requirements bundles whose Bundled Items resolve to no real item file." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  (--apply: writes ## Status: done to every retirable bundle; omit for a" \
        "   report-only dry run that modifies nothing -- the default)" \
        "" \
        "Eligibility rule: a bundle is retirable only when its ## Status is not" \
        "already done or running, AND every line under its ## Bundled Items section" \
        "fails to resolve to an existing file on disk (including zero item lines)." \
        "A bundle with at least one item line resolving to a real file is kept and" \
        "never modified, in either mode. A bundle already done or running is skipped" \
        "and never modified, in either mode."
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional fallback).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: retire-empty-bundles.sh --project <name> [--apply]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# Require --project; emit error for a missing-value flag.
if argparse_missing "project"; then
    echo "ERROR: --project requires a value" >&2
    exit 1
fi

PROJECT_ARG="$(operator_args_project)"
APPLY=0; argparse_has "apply" && APPLY=1

if [[ -z "$PROJECT_ARG" ]]; then
    echo "ERROR: --project is required." >&2
    echo "Usage: retire-empty-bundles.sh --project <name> [--apply]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Bootstrap: self-locate -> source shell-env -> fail loud
# ---------------------------------------------------------------------------
# shellcheck source=lib/env_bootstrap.sh
source "${SCRIPT_DIR}/lib/env_bootstrap.sh" || exit 1

# ---------------------------------------------------------------------------
# Source the library stages this script needs. Only the two focused
# discovery stage libraries are sourced -- not the full discovery.sh driver
# -- because the only primitives used here are the bundle-filename pattern,
# the ## Status field reader/writer, and the ## Status idempotency check
# already used by discovery.sh's own bundle-eligibility scan. Pulling in
# discovery_guards.sh / discovery_items.sh / discovery_steps.sh would load
# HALT-checking and pipeline-stepping machinery this read/report/apply tool
# never calls.
# ---------------------------------------------------------------------------
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/discovery_state.sh
source "${SCRIPT_DIR}/lib/discovery_state.sh"
# shellcheck source=lib/discovery_bundle.sh
source "${SCRIPT_DIR}/lib/discovery_bundle.sh"

# ---------------------------------------------------------------------------
# Enable strict mode for our own code
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve project name and requirements directory.
# ---------------------------------------------------------------------------
PROJECT_NAME="$(pp_resolve_project_name "$PROJECT_ARG")" || {
    echo "" >&2
    echo "ERROR: could not resolve project context for '$PROJECT_ARG'." >&2
    exit 1
}

REQUIREMENTS_DIR="$(pp_requirements_dir "$PROJECT_NAME")" || {
    echo "ERROR: could not resolve requirements directory for '$PROJECT_NAME'" >&2
    exit 1
}

if [[ ! -d "$REQUIREMENTS_DIR" ]]; then
    echo "ERROR: requirements directory not found: $REQUIREMENTS_DIR" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# _reb_classify_items <bundle_file>
# Reads the file's ## Bundled Items section and classifies it.
# Prints two lines:
#   line 1: "retirable" or "kept"
#   line 2: a one-line human-readable detail ("0 item line(s), none resolve
#           to an existing file" / "item resolves to an existing file:
#           <path>")
# A bundle with a Bundled Items section that resolves to no existing file
# for any item line -- including a section with zero item lines -- is
# retirable. A bundle with at least one item line resolving to an existing
# file is kept.
# ---------------------------------------------------------------------------
_reb_classify_items() {
    local bundle_file="$1"
    python3 - "$bundle_file" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")

m = re.search(
    r'^##\s+Bundled Items[ \t]*\n(.*?)(?=\n##|\Z)',
    text,
    flags=re.M | re.S | re.IGNORECASE,
)
body = m.group(1) if m else ""

item_re = re.compile(r'^-\s.*$', re.M)
path_re = re.compile(r'\(`([^`]*)`\)')

total = 0
real = []
for line in item_re.finditer(body):
    total += 1
    pm = path_re.search(line.group(0))
    if not pm:
        continue
    candidate = pm.group(1).strip()
    if candidate and pathlib.Path(candidate).is_file():
        real.append(candidate)

if real:
    print("kept")
    print(f"item resolves to an existing file: {real[0]}")
else:
    print("retirable")
    print(f"{total} item line(s) under Bundled Items, none resolve to an existing file")
PY
}

# ---------------------------------------------------------------------------
# Scan requirements/*.md for bundle files, classify each, report, and (with
# --apply) retire the ones classified retirable.
# ---------------------------------------------------------------------------
considered=0
retirable_count=0
kept_count=0
skipped_count=0
retired_count=0

shopt -s nullglob
bundle_files=("${REQUIREMENTS_DIR}"/*.md)
shopt -u nullglob

for req_file in "${bundle_files[@]}"; do
    fname="$(basename "$req_file")"
    is_requirements_bundle_file "$fname" || continue

    considered=$((considered + 1))

    status="$(_disc_read_bundle_field "$req_file" "Status" 2>/dev/null || echo "open")"
    status="${status,,}"

    echo "file:    ${fname}"
    echo "status:  ${status}"

    if [[ "$status" == "done" || "$status" == "running" ]]; then
        skipped_count=$((skipped_count + 1))
        echo "verdict: skipped (already ${status})"
        echo ""
        continue
    fi

    verdict_detail="$(_reb_classify_items "$req_file")"
    verdict="$(head -n1 <<<"$verdict_detail")"
    detail="$(tail -n1 <<<"$verdict_detail")"
    echo "items:   ${detail}"

    if [[ "$verdict" == "retirable" ]]; then
        retirable_count=$((retirable_count + 1))
        if [[ "$APPLY" -eq 1 ]]; then
            _disc_set_status_field "$req_file" "done"
            retired_count=$((retired_count + 1))
            echo "verdict: retirable -- retired (## Status set to done)"
        else
            echo "verdict: retirable"
        fi
    else
        kept_count=$((kept_count + 1))
        echo "verdict: kept"
    fi
    echo ""
done

if [[ "$considered" -eq 0 ]]; then
    echo "(no bundle files found in ${REQUIREMENTS_DIR})"
fi

if [[ "$APPLY" -eq 1 ]]; then
    echo "summary: ${considered} bundle(s) considered, ${retirable_count} retirable, ${retired_count} retired, ${kept_count} kept, ${skipped_count} skipped"
else
    echo "summary: ${considered} bundle(s) considered, ${retirable_count} retirable, ${kept_count} kept, ${skipped_count} skipped (dry run -- re-run with --apply to retire)"
fi

exit 0
