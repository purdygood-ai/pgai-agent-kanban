#!/usr/bin/env bash
# regenerate-changelog.sh
# Operator-invoked script to rebuild CHANGELOG.md from all release-notes/*.md
# files in a project root.
#
# Usage:
#   regenerate-changelog.sh --project <name> [--output <path>]
#
# Options:
#   --project NAME         Registered project name. The project root is resolved
#                          as $KANBAN_ROOT/projects/<name>/. Required.
#   --output PATH          Output path for CHANGELOG.md.
#                          Default: <project-root>/CHANGELOG.md
#   --help, -h             Print this help and exit
#
# The script:
#   1. Resolves the project root from --project <name>.
#   2. Reads all *.md files in <project-root>/release-notes/ whose names match
#      the pattern v<major>.<minor>.<patch>*.md (semver prefix).
#   3. Sorts them by semantic version (newest first).
#   4. For each file, strips the top-level H1 title line.
#   5. Assembles CHANGELOG.md with a standard header, each release as a
#      ## vX.Y.Z — <date> section (date read from **Release Date:** field).
#   6. Writes the result to the output path and exits 0.
#
# Exits non-zero on error (missing --project, project not found, missing release-notes/ dir).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Resolve kanban root.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project help output h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: project, output.
# Boolean: help.
argparse_parse \
    --value-flags "project output" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "project"; then
    echo "regenerate-changelog.sh: error: --project requires a value" >&2
    exit 1
fi
if argparse_missing "output"; then
    echo "regenerate-changelog.sh: error: --output requires a value" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "regenerate-changelog.sh" \
        "Rebuild CHANGELOG.md from all release-notes/*.md files in a project root." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --output PATH      Output path (default: <project-root>/CHANGELOG.md)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "regenerate-changelog.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: regenerate-changelog.sh --project <name> [--output <path>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "regenerate-changelog.sh" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract flag values
# ---------------------------------------------------------------------------
PROJECT_NAME=""
OUTPUT_PATH=""

if argparse_has "project"; then PROJECT_NAME="${ARGPARSE_FLAGS[project]}"; fi
if argparse_has "output";  then OUTPUT_PATH="${ARGPARSE_FLAGS[output]}"; fi

# ---------------------------------------------------------------------------
# Validate required --project argument
# ---------------------------------------------------------------------------
PROJECT_NAME="$(operator_args_project)"
if [[ -z "$PROJECT_NAME" ]]; then
    echo "regenerate-changelog.sh: error: --project is required (or set PGAI_PROJECT_NAME)" >&2
    echo "Usage: regenerate-changelog.sh --project <name> [--output <path>]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve project root from project name
# ---------------------------------------------------------------------------
PROJECT_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}"

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "regenerate-changelog.sh: error: project '${PROJECT_NAME}' not found (expected: ${PROJECT_DIR})" >&2
    exit 1
fi

RELEASE_NOTES_DIR="${PROJECT_DIR}/release-notes"
if [[ ! -d "$RELEASE_NOTES_DIR" ]]; then
    echo "regenerate-changelog.sh: error: release-notes/ directory not found under: $PROJECT_DIR" >&2
    exit 1
fi

if [[ -z "$OUTPUT_PATH" ]]; then
    OUTPUT_PATH="${PROJECT_DIR}/CHANGELOG.md"
fi

# ---------------------------------------------------------------------------
# Delegate to Python for semver sort and assembly
# ---------------------------------------------------------------------------
python3 - "$RELEASE_NOTES_DIR" "$OUTPUT_PATH" <<'REGEN_PY'
import sys
import re
import pathlib

release_notes_dir = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])

SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)")

HEADER = (
    "# Changelog\n\n"
    "<!-- Auto-generated by cm-release.sh. Do not edit manually. -->\n"
)


def semver_key(p):
    """Return a tuple (major, minor, patch) for semver sorting."""
    m = SEMVER_RE.match(p.stem)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (-1, -1, -1)


def extract_release_date(text):
    """Read **Release Date:** YYYY-MM-DD from file content. Default: 'unknown'."""
    m = re.search(r"\*\*Release Date:\*\*\s*(\S+)", text)
    return m.group(1) if m else "unknown"


def strip_h1_title(text):
    """Remove the first top-level H1 line from file content."""
    lines = text.splitlines()
    result = []
    skipped = False
    for line in lines:
        if not skipped and re.match(r"^#[^#]", line):
            skipped = True
            continue
        result.append(line)
    # Strip leading blank lines left after removing title
    joined = "\n".join(result).lstrip("\n")
    return joined


# Collect matching release notes files
candidates = [
    p for p in release_notes_dir.glob("*.md")
    if SEMVER_RE.match(p.stem)
]

if not candidates:
    print(
        f"WARNING: no versioned release notes files found in {release_notes_dir}",
        flush=True,
    )

# Sort newest first
candidates.sort(key=semver_key, reverse=True)

sections = []
for p in candidates:
    text = p.read_text(encoding="utf-8")
    version = p.stem
    release_date = extract_release_date(text)
    body = strip_h1_title(text)
    sections.append(f"## {version} — {release_date}\n\n{body}")

if sections:
    combined = f"\n\n---\n\n".join(sections)
    content = f"{HEADER}\n---\n\n{combined}\n\n---\n"
else:
    content = f"{HEADER}\n---\n\n*(no releases found)*\n\n---\n"

output_path.write_text(content, encoding="utf-8")
print(f"CHANGELOG.md written to: {output_path}", flush=True)
print(f"Releases included: {len(candidates)}", flush=True)
REGEN_PY
