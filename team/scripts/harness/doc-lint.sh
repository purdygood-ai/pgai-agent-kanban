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
# Every container-engine command goes through the shared helper
# (pgai_docker), the one path kanban code takes to an engine.
# shellcheck source=../lib/docker_use.sh
source "${SCRIPT_DIR}/../lib/docker_use.sh"
# shellcheck source=../lib/pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"
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
    # CONVERT: quality-class harness precondition -- an unnamed suite means
    # the run has nothing to check, not that the docs tree has a defect.
    # CENSUS-ANCHOR: doc-lint-suite-required-01
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
# in the audited doc surface. Extend this array as new fossils are
# identified and swept.
#
#   Pattern calibration notes (to avoid false positives on legitimate docs):
#
#   - /home/<user>/pgai_agent_kanban : live-install path fossil -- a kanban root
#     under one real account's home. Narrowed to the kanban root path itself
#     (not a bare /home/<user>, which config-example snippets use legitimately,
#     e.g. dev_tree_path = /home/<user>/develop/my-app), and the negative
#     lookahead skips the placeholder account names the docs already use
#     (you, user, youruser, your-user, USERNAME, op), so a documented example
#     such as /home/you/pgai_agent_kanban is not a fossil. These patterns are
#     Perl-compatible (grep -P) for that lookahead.
#
#   - Internal bug file absolute paths: the fossil form is an absolute path
#     like /home/<user>/pgai_agent_kanban/projects/.../bugs/BUG-NNN-slug.md.
#     The example form in HOW_TO.md uses a relative path or cp narrative
#     (projects/<name>/bugs/) which is legitimate documentation.
#     Pattern targets only absolute-path bug references with /home/ prefix.
#
#   - v1.23.3 : a specific internal version string that must not appear
#     in forward-looking docs.
# ---------------------------------------------------------------------------
FOSSIL_PATTERNS=(
    "/home/(?!(?i:you|user|youruser|your[-_]user|username|op)/)[A-Za-z0-9_][A-Za-z0-9._-]*/pgai_agent_kanban"
    "/home/[a-z_][a-z0-9_]*/pgai_agent_kanban/projects/.*/bugs/BUG-[0-9]"
    "v1\\.23\\.3"
)

# ---------------------------------------------------------------------------
# Feature grep patterns — tokens that must be present in the doc surface to
# confirm feature coverage. Case-insensitive to handle capitalization variants
# (Debian/debian, Rocky9/rocky9). Keep this list in sync with the flavours the
# tree actually ships under docker/*/Dockerfile — a token for a flavour that
# does not exist trains a reader to ignore this suite's output.
# ---------------------------------------------------------------------------
FEATURE_PATTERNS=(
    "debian"
    "rocky9"
    "branch_prefix"
    "push_to_remote"
    "dev_tree_path"
    "wake-batch.sh"
    "install.sh"
    # blocked_minutes: the rc-metrics Blocked column (history.csv's own
    # column name) -- guards against a future edit that drops HOW_TO.md's
    # operator-facing mention (or docs/OPERATIONS.md's / docs/dashboard.md's,
    # which cite the same token) without anyone noticing the doc-truth gap.
    "blocked_minutes"
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
    if grep -qE '<[^[:space:]>]+>' <<< "$cmd"; then
        return 0
    fi

    # b. Ellipsis-dot paths: .../something or bare ... used as path fragment
    #    Catches: echo rc:v0.62.0 > .../HALT-AFTER  and similar
    if grep -qE '(\.\.\./)' <<< "$cmd"; then
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
    if grep -qE '^\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}$' <<< "$first_token"; then
        return 0
    fi

    # d. $EDITOR invocation: interactive editor command; cannot run in non-TTY environment
    if [[ "$first_token" == '$EDITOR' ]]; then
        return 0
    fi
    if grep -qE '^\$\{EDITOR' <<< "$first_token"; then
        return 0
    fi

    # e. /path/to/ fragment: generic placeholder path in any token
    #    Catches: /path/to/foo, /path/to/something, etc.
    if grep -qF '/path/to/' <<< "$cmd"; then
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
    if ! grep -qE '^docker\s+compose\s+.*\bexec\b' <<< "$cmd"; then
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
    pgai_docker docker compose ps --status running "$service" > /dev/null 2>&1 || probe_exit=$?

    if [[ "$probe_exit" -ne 0 ]]; then
        # Service not running (or docker unavailable) — caller should SKIP
        return 0
    fi

    # Service IS running — caller should execute the command normally
    return 1
}

# ---------------------------------------------------------------------------
# Fenced-block extractor
#
# The fenced bash/sh commands of a Markdown file, with their annotations
# (pgai_agent_kanban.lint.doc_commands): one line per command,
#   <block number>\t<block_skip>\t<docker_gate>\t<inline_skip>\t<command>
# with the flags "true" or "false" --
#   - block_skip: the block is preceded by <!-- doc-lint: skip ... -->
#   - docker_gate: the block is preceded by <!-- doc-lint: docker -->
#   - inline_skip: the command line ends with  # doc-lint: skip  (removed
#     from the command text)
# Backslash continuations are joined into one command.
# ---------------------------------------------------------------------------
_extract_fenced_blocks() {
    local filepath="$1"
    pp_run_ops pgai_agent_kanban.lint.doc_commands "$filepath"
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

        # Process each command the extractor reports. A block-skip block's
        # commands are all skipped; a docker-gated block's are all skipped
        # when no container engine answers (asked once per block). The
        # extractor's lines arrive on fd 3, so a command that reads stdin
        # cannot consume them.
        local gated_block="" docker_down=false
        local block_no block_skip docker_gate inline_skip cmd_text
        while IFS=$'\t' read -r -u 3 block_no block_skip docker_gate inline_skip cmd_text; do
            # Block-level skip annotation
            if [[ "$block_skip" == "true" ]]; then
                [[ "$VERBOSE" == "true" ]] && echo "  SKIP-BLOCK: ${cmd_text}"
                file_skip=$((file_skip + 1))
                continue
            fi

            # Block-level docker gate
            if [[ "$docker_gate" == "true" ]]; then
                if [[ "$block_no" != "$gated_block" ]]; then
                    gated_block="$block_no"
                    docker_down=false
                    pgai_docker docker info > /dev/null 2>&1 || docker_down=true
                fi
                if [[ "$docker_down" == "true" ]]; then
                    [[ "$VERBOSE" == "true" ]] && echo "  SKIP-DOCKER: ${cmd_text}"
                    file_skip=$((file_skip + 1))
                    continue
                fi
                # Docker is available — fall through to per-command execution
            fi

            # Per-command processing
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
                # Non-deterministic (exempt): a non-zero exit can arise from a
                # stale command, a missing skip annotation, or absent external
                # state — the correct resolution is not computable at
                # detection. Per docs/coding-standards.md "The
                # non-deterministic exemption", this is exempt from a numbered
                # fix-command block but never exempt from naming the next
                # diagnostic step.
                echo "  FAIL (exit ${exit_code}): ${cmd_text}"
                echo "    in: ${rel_path}"
                echo "    Cause: this fenced command exited ${exit_code} when executed verbatim from ${DOC_ROOT}."
                echo "    Next diagnostic step: re-run it directly to see its full output:"
                echo "      (cd \"${DOC_ROOT}\" && ${cmd_text})"
                echo "    If it legitimately requires unavailable external state (a running service, an"
                echo "    interactive editor, credentials), mark it non-executable with one of the three"
                echo "    doc-lint skip mechanisms documented in this script's --help output, rather than"
                echo "    leaving the doc fixture failing."
                file_fail=$((file_fail + 1))
                any_fail=true
            fi
        done 3< <(_extract_fenced_blocks "$filepath")

        total_pass=$((total_pass + file_pass))
        total_skip=$((total_skip + file_skip))
        total_fail=$((total_fail + file_fail))
    done

    echo "=== cmd-pass summary: files=${total_files} pass=${total_pass} skip=${total_skip} fail=${total_fail} ==="
    if [[ "$any_fail" == "true" ]]; then
        echo "=== doc-lint.sh: FAILURES DETECTED — see above ==="
        echo ""
        echo "Rule: every fenced bash/sh command in the audited doc surface must execute"
        echo "successfully as documented, or be explicitly marked non-executable via a"
        echo "doc-lint skip/docker annotation (see --help)."
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
                # Partial: the gate can name the exact cause and location and
                # locate the offending line deterministically, but the corrected
                # target is genuinely free-form (only the author knows the
                # intended destination) — per docs/coding-standards.md, print a
                # concrete, filled-in example rather than a bare placeholder.
                echo "  DEAD LINK: ${link}"
                echo "    in: ${rel_path}"
                echo "    resolved to: ${resolved}"
                echo "    Cause: ${rel_path} links to '${link}', which resolves to a path that does not exist."
                echo "    To fix:"
                echo "      1. grep -n -F '${link}' \"${filepath}\""
                echo "      2. Edit the line found above so the link target points at the correct existing"
                echo "         file (it currently resolves to '${resolved}', which is absent)."
                echo "      3. ${BASH_SOURCE[0]} --suite links --doc-root \"${DOC_ROOT}\""
                echo "    Rule: every relative Markdown link in the audited doc surface must resolve to an existing file."
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
            hits=$(grep -nP "$pattern" "$filepath" 2>/dev/null || true)
            if [[ -n "$hits" ]]; then
                # The header names the residue actually found (the first
                # match); the Cause line below names the pattern it matched.
                local found
                found=$(grep -oP -m1 "$pattern" "$filepath" 2>/dev/null | head -n1 || true)
                echo "  FOSSIL HIT: pattern=${found:-$pattern}  file=${rel_path}"
                # Partial: the gate names the exact file:line and matched
                # pattern deterministically, but the surrounding rewrite that
                # removes the residue is author content — print a filled-in
                # example command block ending in the re-check, per
                # docs/coding-standards.md.
                echo "$hits" | while IFS= read -r hit_line; do
                    hit_lineno="${hit_line%%:*}"
                    echo "    ${hit_line}"
                    echo "    Cause: ${rel_path}:${hit_lineno} matches the forbidden internal-residue pattern '${pattern}'."
                    echo "    To fix:"
                    echo "      1. sed -n '${hit_lineno}p' \"${filepath}\""
                    echo "      2. Remove or rewrite that line in ${rel_path} so it no longer matches '${pattern}'."
                    echo "      3. ${BASH_SOURCE[0]} --suite fossil-greps --doc-root \"${DOC_ROOT}\""
                    echo "    Rule: none of the known internal-residue patterns (FOSSIL_PATTERNS in ${BASH_SOURCE[0]}) may appear in the audited doc surface."
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
            # Partial (must-find gate): the gate names the missing token
            # deterministically, but authoring the documentation section that
            # introduces it is free-form content only a human can write — print
            # a filled-in example of where/how to add it, per
            # docs/coding-standards.md.
            echo "  FEATURE MISSING: pattern=${pattern} not found in any audited doc"
            echo "    Cause: no file in the audited doc surface contains a case-insensitive match for '${pattern}'."
            echo "    To fix:"
            echo "      1. grep -rniE '${pattern}' \"${DOC_ROOT}\"   # confirm it is truly undocumented, not just outside the audited surface"
            echo "      2. Add a section documenting '${pattern}' to one of the audited docs (e.g. README.md or HOW_TO.md)."
            echo "      3. ${BASH_SOURCE[0]} --suite feature-greps --doc-root \"${DOC_ROOT}\""
            echo "    Rule: every required feature token in FEATURE_PATTERNS (${BASH_SOURCE[0]}) must be documented somewhere in the audited doc surface."
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
        # CONVERT: quality-class harness precondition -- same reasoning as
        # doc-lint-suite-required-01, for an unrecognized (not merely absent)
        # suite name.
        # CENSUS-ANCHOR: doc-lint-suite-unknown-01
        exit 2
        ;;
esac
