#!/usr/bin/env bash
# team/scripts/harness/doc-lint.sh
#
# Doc-lint harness — reusable documentation fixture for kanban doc RCs.
#
# ===========================================================================
# USAGE
# ===========================================================================
#
# Runs one of four lint passes over the audited doc surface:
#
#   doc-lint.sh [OPTIONS]
#
# OPTIONS
#   --suite <name>    Which pass to run (required). One of:
#                       cmd           Execute fenced bash/sh commands verbatim.
#                       links         Verify relative Markdown links resolve.
#                       fossil-greps  Grep for known internal-residue patterns (zero-hit gate).
#                       feature-greps Grep for required feature tokens (must-find gate).
#   --doc-root <p>    Root directory to scan (default: repo root, auto-detected).
#   --verbose         Print each command/link/grep before its result.
#   --help            Print this message and exit 0.
#
# EXIT CODES
#   0   All checks in the selected suite passed (or --help requested).
#   1   One or more checks in the selected suite failed.
#   2   Invocation error (unknown suite, missing argument, etc.).
#
# SKIP MECHANISMS (cmd suite)
#   The cmd suite uses three explicit skip mechanisms and one auto-placeholder
#   heuristic to avoid failing commands that legitimately require external state.
#
#   1. Block-level skip:
#        <!-- doc-lint: skip — <reason> -->
#        (Line immediately before the opening fence. Skips the entire block.)
#
#   2. Inline skip (single command within a block):
#        some-command arg  # doc-lint: skip
#
#   3. Block-level docker gate:
#        <!-- doc-lint: docker -->
#        (Skips the entire block when the Docker daemon is unavailable.)
#
#   4. Auto-placeholder heuristic — _is_placeholder_cmd():
#      A command is auto-skipped (SKIP-PLACEHOLDER) when any of these patterns
#      match:
#        a. Angle-bracket tokens:   command contains <...> or <foo>
#        b. Ellipsis-dot paths:     command contains .../  (e.g. echo rc > .../HALT-AFTER)
#        c. Bare variable command:  entire first token is a bare ${VAR} or ${VAR:-default}
#           substitution (e.g. ${EDITOR:-vi} file.yaml)
#        d. $EDITOR invocation:     command starts with $EDITOR or "${EDITOR" (interactive;
#           cannot run in a non-TTY harness environment)
#        e. /path/to/ fragment:     any token contains /path/to/ (generic path placeholder)
#
#   5. Compose-exec skip class — _is_compose_exec_unavailable():
#      A command matching `docker compose ... exec <service> ...` is probed by
#      running `docker compose ps --status running <service>`. When the service
#      is not running the command receives SKIP-SERVICE narration. When the
#      service IS running the command executes normally and its actual exit code
#      is reported (pass or fail).
#
# AUDITED DOC SURFACE
#   The following five files are the standing audited surface for kanban doc RCs:
#     README.md
#     HOW_TO.md
#     docs/docker.md
#     team/demos/chomp-man-demo/README.md
#     team/demos/three-bears-demo/README.md
#
# ===========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Self-locate — each tree finds itself; never import from a sibling tree
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SUITE=""
DOC_ROOT="${REPO_ROOT}"
VERBOSE=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            awk '/^set -euo pipefail/{exit} /^#/{sub(/^# ?/,""); print}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        --suite)
            SUITE="${2:-}"
            shift 2
            ;;
        --doc-root)
            DOC_ROOT="${2:-}"
            shift 2
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        *)
            echo "doc-lint.sh: unknown argument: $1" >&2
            echo "  Run with --help for usage." >&2
            exit 2
            ;;
    esac
done

if [[ -z "${SUITE}" ]]; then
    echo "doc-lint.sh: --suite is required." >&2
    echo "  Valid suites: cmd, links, fossil-greps, feature-greps" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Audited doc surface (paths relative to DOC_ROOT)
# ---------------------------------------------------------------------------
AUDITED_DOCS=(
    "README.md"
    "HOW_TO.md"
    "docs/docker.md"
    "team/demos/chomp-man-demo/README.md"
    "team/demos/three-bears-demo/README.md"
)

# ---------------------------------------------------------------------------
# Fossil grep patterns — known internal-residue strings that must not appear
# in the audited doc surface. These are the patterns swept in the v1.26.4
# WRITER pass; extend this array as new fossils are identified and swept.
#
#   Pattern calibration notes (to avoid false positives on legitimate docs):
#
#   - /home/rocky/pgai_agent_kanban : live-install path fossil; narrowed to the
#     actual kanban root path (not bare /home/rocky which appears legitimately
#     in config-example snippets like dev_tree_path = /home/rocky/develop/my-app)
#
#   - Internal bug file absolute paths: the fossil form is an absolute path
#     like /home/rocky/pgai_agent_kanban/projects/.../bugs/BUG-NNN-slug.md.
#     The example form in HOW_TO.md uses a relative path or cp narrative
#     (projects/<name>/bugs/) which is legitimate documentation.
#     Pattern targets only absolute-path bug references with /home/ prefix.
#
#   - v1.23.3 : specific internal version string from pre-flatten era;
#     should not appear in forward-looking docs.
# ---------------------------------------------------------------------------
FOSSIL_PATTERNS=(
    "/home/rocky/pgai_agent_kanban"
    "/home/[a-z_][a-z0-9_]*/pgai_agent_kanban/projects/.*/bugs/BUG-[0-9]"
    "v1\\.23\\.3"
)

# ---------------------------------------------------------------------------
# Feature grep patterns — tokens that must be present in the doc surface to
# confirm feature coverage. Case-insensitive to handle capitalization variants
# (Debian/debian, RHEL9/rhel9, UBI9/ubi9).
# ---------------------------------------------------------------------------
FEATURE_PATTERNS=(
    "debian"
    "rhel9"
    "ubi9"
    "branch_prefix"
    "push_to_remote"
    "dev_tree_path"
    "wake-batch.sh"
    "install.sh"
)

# ---------------------------------------------------------------------------
# _is_placeholder_cmd <command>
#
# Returns 0 (true) when the command is a recognized auto-placeholder that
# should be skipped rather than executed. Each pattern class is documented
# with a one-line comment naming what it catches.
#
# Extended placeholder classes:
#   a. Angle-bracket tokens — classic <placeholder> or <foo-bar> shape
#   b. Ellipsis-dot paths   — .../ notation for omitted path prefix
#   c. Bare variable command — ${VAR} or ${VAR:-default} as entire first token
#   d. $EDITOR invocation   — interactive editor; cannot run in non-TTY harness
#   e. /path/to/ fragment   — generic /path/to/ placeholder in any token
# ---------------------------------------------------------------------------
_is_placeholder_cmd() {
    local cmd="$1"

    # a. Angle-bracket tokens: <foo>, <placeholder>, <repo-url>, etc.
    # Use grep -qE to avoid bash ERE word-boundary issues with < and > in [[ =~ ]]
    if echo "$cmd" | grep -qE '<[^[:space:]>]+>'; then
        return 0
    fi

    # b. Ellipsis-dot paths: .../something or bare ... used as path fragment
    #    Catches: echo rc:v0.62.0 > .../HALT-AFTER  and similar
    if echo "$cmd" | grep -qE '(\.\.\./)'; then
        return 0
    fi
    # Also catch a standalone ... as the entire command (rare but valid placeholder)
    if [[ "$cmd" == "..." ]]; then
        return 0
    fi

    # c. Bare variable command: first token is entirely a ${VAR} or ${VAR:-default}
    #    substitution (i.e. the command IS the variable, e.g. ${EDITOR:-vi} file.cfg)
    local first_token
    first_token="$(echo "$cmd" | awk '{print $1}')"
    if echo "$first_token" | grep -qE '^\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}$'; then
        return 0
    fi

    # d. $EDITOR invocation: interactive editor command; cannot run in non-TTY environment
    if [[ "$first_token" == '$EDITOR' ]]; then
        return 0
    fi
    if echo "$first_token" | grep -qE '^\$\{EDITOR'; then
        return 0
    fi

    # e. /path/to/ fragment: generic placeholder path in any token
    #    Catches: /path/to/foo, /path/to/something, etc.
    if echo "$cmd" | grep -qF '/path/to/'; then
        return 0
    fi

    return 1
}

# ---------------------------------------------------------------------------
# _is_compose_exec_unavailable <command>
#
# Compose-exec skip class: detects `docker compose ... exec <service> ...`
# commands and probes whether the named service is currently running.
#
# Returns 0 (true) when the command is a docker compose exec invocation AND
# the target service is NOT running (service unavailable → skip, not fail).
# Returns 1 (false) when the service IS running or the command is not a
# docker compose exec at all (command executes normally).
#
# This degrades gracefully: commands are skipped only when the service cannot
# be reached. When the service is up, execution proceeds normally and the
# actual exit code is reported.
# ---------------------------------------------------------------------------
_is_compose_exec_unavailable() {
    local cmd="$1"

    # Match: docker compose [options] exec <service> <cmd...>
    # The service name follows "exec" with optional flags (-T, -u user, etc.)
    if ! echo "$cmd" | grep -qE '^docker\s+compose\s+.*\bexec\b'; then
        return 1
    fi

    # Extract the service name — first non-flag word after "exec"
    local service
    service=$(echo "$cmd" | sed -E 's/.*\bexec\b[[:space:]]+((-[a-zA-Z]+|--[a-z-]+(=[^[:space:]]+)?)[[:space:]]+)*//' | awk '{print $1}')

    if [[ -z "$service" ]]; then
        return 1
    fi

    # Probe: check if the service is running via docker compose ps.
    # Suppress all output; we only care about exit code.
    # docker compose ps --status running exits 0 when service is listed, non-0 otherwise.
    # Protect against docker not being installed: if docker exits with command-not-found (127),
    # treat as service unavailable.
    local probe_exit=0
    docker compose ps --status running "$service" > /dev/null 2>&1 || probe_exit=$?

    if [[ "$probe_exit" -ne 0 ]]; then
        # Service not running (or docker unavailable) — caller should SKIP
        return 0
    fi

    # Service IS running — caller should execute the command normally
    return 1
}

# ---------------------------------------------------------------------------
# Python fenced-block extractor
#
# Reads a Markdown file and yields fenced bash/sh command blocks with their
# metadata:
#   - block_skip: true if immediately preceded by <!-- doc-lint: skip ... -->
#   - docker_gate: true if immediately preceded by <!-- doc-lint: docker -->
#   - commands: list of logical commands (backslash continuations joined)
#
# Output format (one JSON object per block, newline-delimited):
#   {"block_skip": bool, "docker_gate": bool, "commands": ["cmd1", "cmd2"]}
#
# Inline skip: a command line ending with  # doc-lint: skip  is emitted with
# the "inline_skip" key set to true.
# ---------------------------------------------------------------------------
_extract_fenced_blocks() {
    local filepath="$1"
    python3 - "$filepath" <<'PYEOF'
import sys
import json
import re

filepath = sys.argv[1]
try:
    text = open(filepath, encoding="utf-8").read()
except OSError as e:
    print(f"doc-lint.sh: cannot read {filepath}: {e}", file=sys.stderr)
    sys.exit(1)

lines = text.splitlines()

def join_continuations(raw_lines):
    """Join backslash-continuation lines into logical commands."""
    joined = []
    buf = ""
    for line in raw_lines:
        if line.endswith("\\"):
            buf += line[:-1] + " "
        else:
            buf += line
            joined.append(buf.strip())
            buf = ""
    if buf.strip():
        joined.append(buf.strip())
    return [l for l in joined if l]

i = 0
while i < len(lines):
    line = lines[i]

    # Detect block-level annotations on the line immediately before a fence
    block_skip = False
    docker_gate = False
    if re.match(r'<!--\s*doc-lint:\s*skip', line, re.IGNORECASE):
        block_skip = True
    elif re.match(r'<!--\s*doc-lint:\s*docker\s*-->', line, re.IGNORECASE):
        docker_gate = True

    # Look for a fenced code block opening (``` or ~~~ with optional lang)
    fence_match = None
    if block_skip or docker_gate:
        # The fence must be on the very next non-blank line
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j < len(lines):
            fence_match = re.match(r'^(`{3,}|~{3,})\s*(bash|sh)?\s*$', lines[j])
            if fence_match:
                i = j  # advance to the fence line
    else:
        fence_match = re.match(r'^(`{3,}|~{3,})\s*(bash|sh)?\s*$', line)
        if not fence_match:
            i += 1
            continue

    if not fence_match:
        i += 1
        continue

    # Fence detected — determine if it's a bash/sh block
    fence_char = fence_match.group(1)[0]
    fence_len = len(fence_match.group(1))
    lang = (fence_match.group(2) or "").strip().lower()

    if lang not in ("bash", "sh", ""):
        # Non-bash block; skip to closing fence
        i += 1
        while i < len(lines):
            if re.match(rf'^[{re.escape(fence_char)}]{{{fence_len},}}\s*$', lines[i]):
                break
            i += 1
        i += 1
        continue

    # Only process bash/sh blocks (lang == "bash" or "sh")
    # Empty lang is ambiguous — include it if the annotation context marked it,
    # otherwise include it only if block_skip / docker_gate
    if lang == "" and not block_skip and not docker_gate:
        # Unlabelled block: include it only if it's being annotated; otherwise skip
        i += 1
        while i < len(lines):
            if re.match(rf'^[{re.escape(fence_char)}]{{{fence_len},}}\s*$', lines[i]):
                break
            i += 1
        i += 1
        continue

    # Collect raw lines until closing fence
    i += 1
    raw_lines = []
    while i < len(lines):
        if re.match(rf'^[{re.escape(fence_char)}]{{{fence_len},}}\s*$', lines[i]):
            break
        raw_lines.append(lines[i])
        i += 1
    i += 1  # skip closing fence

    # Parse inline skips and filter comment-only lines
    commands = []
    for raw in join_continuations(raw_lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        inline_skip = bool(re.search(r'#\s*doc-lint:\s*skip', stripped))
        # Strip the inline skip annotation from the command text
        cmd_text = re.sub(r'\s*#\s*doc-lint:\s*skip.*$', '', stripped).strip()
        if cmd_text:
            commands.append({"cmd": cmd_text, "inline_skip": inline_skip})

    if commands or block_skip or docker_gate:
        print(json.dumps({
            "block_skip": block_skip,
            "docker_gate": docker_gate,
            "commands": commands
        }))

PYEOF
}

# ---------------------------------------------------------------------------
# Suite: cmd — execute fenced bash/sh commands
# ---------------------------------------------------------------------------
_suite_cmd() {
    local total_files=0 total_pass=0 total_skip=0 total_fail=0
    local any_fail=false

    for rel_path in "${AUDITED_DOCS[@]}"; do
        local filepath="${DOC_ROOT}/${rel_path}"
        if [[ ! -f "$filepath" ]]; then
            echo "doc-lint.sh: WARNING: audited file not found: ${filepath}" >&2
            continue
        fi

        total_files=$((total_files + 1))
        local file_pass=0 file_skip=0 file_fail=0

        # Run the Python extractor and process each block
        while IFS= read -r block_json; do
            local block_skip docker_gate
            block_skip=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d['block_skip'] else 'false')")
            docker_gate=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d['docker_gate'] else 'false')")

            # Block-level skip annotation
            if [[ "$block_skip" == "true" ]]; then
                local cmd_count
                cmd_count=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['commands']))")
                for ((n=0; n<cmd_count; n++)); do
                    local cmd_text
                    cmd_text=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'][$n]['cmd'])")
                    [[ "$VERBOSE" == "true" ]] && echo "  SKIP-BLOCK: ${cmd_text}"
                    file_skip=$((file_skip + 1))
                done
                continue
            fi

            # Block-level docker gate
            if [[ "$docker_gate" == "true" ]]; then
                if ! docker info > /dev/null 2>&1; then
                    local cmd_count
                    cmd_count=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['commands']))")
                    for ((n=0; n<cmd_count; n++)); do
                        local cmd_text
                        cmd_text=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'][$n]['cmd'])")
                        [[ "$VERBOSE" == "true" ]] && echo "  SKIP-DOCKER: ${cmd_text}"
                        file_skip=$((file_skip + 1))
                    done
                    continue
                fi
                # Docker is available — fall through to per-command execution
            fi

            # Per-command processing
            local cmd_count
            cmd_count=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['commands']))")
            for ((n=0; n<cmd_count; n++)); do
                local cmd_text inline_skip
                cmd_text=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'][$n]['cmd'])")
                inline_skip=$(echo "$block_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d['commands'][$n]['inline_skip'] else 'false')")

                [[ "$VERBOSE" == "true" ]] && echo "  CMD: ${cmd_text}"

                # Inline skip annotation
                if [[ "$inline_skip" == "true" ]]; then
                    [[ "$VERBOSE" == "true" ]] && echo "  SKIP-INLINE: ${cmd_text}"
                    file_skip=$((file_skip + 1))
                    continue
                fi

                # Auto-placeholder heuristic (extended)
                if _is_placeholder_cmd "$cmd_text"; then
                    [[ "$VERBOSE" == "true" ]] && echo "  SKIP-PLACEHOLDER: ${cmd_text}"
                    file_skip=$((file_skip + 1))
                    continue
                fi

                # Compose-exec skip class — probe service availability
                if _is_compose_exec_unavailable "$cmd_text"; then
                    [[ "$VERBOSE" == "true" ]] && echo "  SKIP-SERVICE: ${cmd_text}"
                    file_skip=$((file_skip + 1))
                    continue
                fi

                # Execute the command in a subshell
                local exit_code=0
                (eval "$cmd_text") > /dev/null 2>&1 || exit_code=$?

                if [[ "$exit_code" -eq 0 ]]; then
                    [[ "$VERBOSE" == "true" ]] && echo "  PASS: ${cmd_text}"
                    file_pass=$((file_pass + 1))
                else
                    echo "  FAIL (exit ${exit_code}): ${cmd_text}"
                    echo "    in: ${rel_path}"
                    file_fail=$((file_fail + 1))
                    any_fail=true
                fi
            done
        done < <(_extract_fenced_blocks "$filepath")

        total_pass=$((total_pass + file_pass))
        total_skip=$((total_skip + file_skip))
        total_fail=$((total_fail + file_fail))
    done

    echo "=== cmd-pass summary: files=${total_files} pass=${total_pass} skip=${total_skip} fail=${total_fail} ==="
    if [[ "$any_fail" == "true" ]]; then
        echo "=== doc-lint.sh: FAILURES DETECTED — see above ==="
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Suite: links — verify relative Markdown links resolve
# ---------------------------------------------------------------------------
_suite_links() {
    local total_files=0 total_pass=0 total_fail=0
    local any_fail=false

    for rel_path in "${AUDITED_DOCS[@]}"; do
        local filepath="${DOC_ROOT}/${rel_path}"
        if [[ ! -f "$filepath" ]]; then
            continue
        fi
        total_files=$((total_files + 1))
        local file_dir
        file_dir="$(dirname "$filepath")"

        # Extract relative links: [text](link) where link does not start with http/https/ftp/#
        while IFS= read -r link; do
            [[ -z "$link" ]] && continue
            # Strip any fragment (#anchor) for file-existence check
            local link_no_fragment="${link%%#*}"
            local resolved="${file_dir}/${link_no_fragment}"
            # Normalize path
            resolved="$(cd "${file_dir}" && realpath -m "${link_no_fragment}" 2>/dev/null || echo "${file_dir}/${link_no_fragment}")"

            [[ "$VERBOSE" == "true" ]] && echo "  LINK: ${link} → ${resolved}"

            if [[ -e "$resolved" ]]; then
                total_pass=$((total_pass + 1))
            else
                echo "  DEAD LINK: ${link}"
                echo "    in: ${rel_path}"
                echo "    resolved to: ${resolved}"
                total_fail=$((total_fail + 1))
                any_fail=true
            fi
        done < <(
            grep -oE '\[([^]]+)\]\(([^)]+)\)' "$filepath" \
            | grep -oE '\(([^)]+)\)' \
            | tr -d '()' \
            | grep -vE '^(https?://|ftp://|#|mailto:)' \
            || true
        )
    done

    echo "=== links summary: files=${total_files} pass=${total_pass} fail=${total_fail} ==="
    if [[ "$any_fail" == "true" ]]; then
        echo "=== doc-lint.sh: FAILURES DETECTED — see above ==="
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Suite: fossil-greps — grep audited docs for known internal-residue patterns
# (zero-hit gate: any match is a failure)
# ---------------------------------------------------------------------------
_suite_fossil_greps() {
    local total_fail=0
    local any_fail=false

    for pattern in "${FOSSIL_PATTERNS[@]}"; do
        [[ "$VERBOSE" == "true" ]] && echo "  FOSSIL-PATTERN: ${pattern}"
        for rel_path in "${AUDITED_DOCS[@]}"; do
            local filepath="${DOC_ROOT}/${rel_path}"
            [[ ! -f "$filepath" ]] && continue
            local hits
            hits=$(grep -nE "$pattern" "$filepath" 2>/dev/null || true)
            if [[ -n "$hits" ]]; then
                echo "  FOSSIL HIT: pattern=${pattern}  file=${rel_path}"
                echo "$hits" | while IFS= read -r hit_line; do
                    echo "    ${hit_line}"
                done
                total_fail=$((total_fail + 1))
                any_fail=true
            fi
        done
    done

    echo "=== fossil-greps summary: fail=${total_fail} ==="
    if [[ "$any_fail" == "true" ]]; then
        echo "=== doc-lint.sh: FAILURES DETECTED — see above ==="
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Suite: feature-greps — grep audited docs for required feature tokens
# (must-find gate: missing token is a failure)
# ---------------------------------------------------------------------------
_suite_feature_greps() {
    local total_fail=0
    local any_fail=false

    for pattern in "${FEATURE_PATTERNS[@]}"; do
        [[ "$VERBOSE" == "true" ]] && echo "  FEATURE-PATTERN: ${pattern}"
        local found=false
        for rel_path in "${AUDITED_DOCS[@]}"; do
            local filepath="${DOC_ROOT}/${rel_path}"
            [[ ! -f "$filepath" ]] && continue
            if grep -qiE "$pattern" "$filepath" 2>/dev/null; then
                found=true
                break
            fi
        done
        if [[ "$found" == "false" ]]; then
            echo "  FEATURE MISSING: pattern=${pattern} not found in any audited doc"
            total_fail=$((total_fail + 1))
            any_fail=true
        fi
    done

    echo "=== feature-greps summary: fail=${total_fail} ==="
    if [[ "$any_fail" == "true" ]]; then
        echo "=== doc-lint.sh: FAILURES DETECTED — see above ==="
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Dispatch to selected suite
# ---------------------------------------------------------------------------
case "$SUITE" in
    cmd)
        _suite_cmd
        ;;
    links)
        _suite_links
        ;;
    fossil-greps)
        _suite_fossil_greps
        ;;
    feature-greps)
        _suite_feature_greps
        ;;
    *)
        echo "doc-lint.sh: unknown suite: ${SUITE}" >&2
        echo "  Valid suites: cmd, links, fossil-greps, feature-greps" >&2
        exit 2
        ;;
esac
