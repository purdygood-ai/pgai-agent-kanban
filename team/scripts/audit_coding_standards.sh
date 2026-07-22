#!/usr/bin/env bash
# team/scripts/audit_coding_standards.sh
#
# Standards audit: run one check per directive in docs/coding-standards.md and
# emit a machine-readable results file with verdicts (PASS / FINDINGS /
# MANUAL-REVIEW-REQUIRED) and offending paths.
#
# Invokes the existing lint scripts (lint_help_presence.py,
# lint_comment_provenance.py, lint_env_bootstrap.py, lint_lib_function_dedupe.py)
# for directives they already cover and runs direct checks for the rest.
#
# Usage:
#   audit_coding_standards.sh [OPTIONS]
#
# Options:
#   --repo-root PATH    Explicit path to the repository root.
#                       Default: inferred from this script's location (two
#                       directories above team/scripts/).
#   --output-file PATH  Where to write the machine-readable results file.
#                       Default: <repo-root>/artifacts/coding-standards-audit-data.md
#   --verbose, -v       Print each directive's check output while running.
#   --help, -h          Print this message and exit 0.
#
# Exit codes:
#   0  No MANUAL-REVIEW-REQUIRED findings in the results file.
#   1  One or more directives received MANUAL-REVIEW-REQUIRED verdict, OR one
#      or more FINDINGS directives warrant human attention.
#   2  Usage error or fatal setup failure (missing repo root, etc.).
#
# Output format (results file):
#   One section per directive, numbered D01 through D11.  Each section has:
#     ## D<NN> — <short directive title>
#     Verdict: PASS | FINDINGS | MANUAL-REVIEW-REQUIRED
#     <verdict-specific body: offending paths, check notes, or review guidance>
#
# Examples:
#   # From repo root:
#   bash team/scripts/audit_coding_standards.sh
#
#   # With explicit output path:
#   bash team/scripts/audit_coding_standards.sh \
#     --output-file /tmp/audit-data.md
#
#   # Verbose (shows each lint's output while running):
#   bash team/scripts/audit_coding_standards.sh --verbose

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory and repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
REPO_ROOT=""
OUTPUT_FILE=""
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --repo-root requires a PATH argument" >&2
        exit 2
      fi
      REPO_ROOT="$2"
      shift 2
      ;;
    --output-file)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --output-file requires a PATH argument" >&2
        exit 2
      fi
      OUTPUT_FILE="$2"
      shift 2
      ;;
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --help|-h)
      cat <<'HELPTEXT'
Usage:
  audit_coding_standards.sh [OPTIONS]

Run one check per directive in docs/coding-standards.md and write a
machine-readable results file with verdicts (PASS / FINDINGS /
MANUAL-REVIEW-REQUIRED) and offending paths or check notes.

Options:
  --repo-root PATH    Explicit path to the repository root.
                      Default: inferred from this script's location (two
                      directories above team/scripts/).
  --output-file PATH  Where to write the machine-readable results file.
                      Default: <repo-root>/docs/coding-standards-audit-data.md
  --verbose, -v       Print each directive's check output while running.
  --help, -h          Print this message and exit 0.

Exit codes:
  0  No MANUAL-REVIEW-REQUIRED or FINDINGS verdicts in the results file.
  1  One or more directives received FINDINGS or MANUAL-REVIEW-REQUIRED verdict.
  2  Usage error or fatal setup failure (missing repo root, etc.).

Output file format:
  One section per directive (D01-D11).  Each section has:
    ## D<NN> — <short directive title>
    Verdict: PASS | FINDINGS | MANUAL-REVIEW-REQUIRED
    <verdict body: offending paths, check notes, or review guidance>

This script delegates to existing lint tools for directives they cover:
  D01  lint_lib_function_dedupe.py (duplicate function names)
  D05  lint_env_bootstrap.py (env-bootstrap ordering violations)
  D06  lint_help_presence.py (missing --help flags)
  D08  lint_comment_provenance.py (provenance references in code)
Other directives receive structural checks or MANUAL-REVIEW-REQUIRED verdicts.

Examples:
  # From repo root:
  bash team/scripts/audit_coding_standards.sh

  # Verbose (shows each check's output inline):
  bash team/scripts/audit_coding_standards.sh --verbose

  # Explicit repo root and output path:
  bash team/scripts/audit_coding_standards.sh \
    --repo-root /path/to/repo \
    --output-file /tmp/audit-results.md
HELPTEXT
      exit 0
      ;;
    *)
      echo "ERROR: unrecognized argument: $1" >&2
      echo "Run with --help for usage." >&2
      exit 2
      ;;
  esac
done

# Apply defaults now that flags are parsed.
if [[ -z "$REPO_ROOT" ]]; then
  REPO_ROOT="$DEFAULT_REPO_ROOT"
fi

if [[ -z "$OUTPUT_FILE" ]]; then
  OUTPUT_FILE="${REPO_ROOT}/docs/coding-standards-audit-data.md"
fi

# ---------------------------------------------------------------------------
# Validate repo root
# ---------------------------------------------------------------------------
if [[ ! -d "$REPO_ROOT" ]]; then
  echo "ERROR: repo root not found: $REPO_ROOT" >&2
  echo "Pass --repo-root to specify the repository root explicitly." >&2
  exit 2
fi

TEAM_DIR="${REPO_ROOT}/team"
SCRIPTS_DIR="${TEAM_DIR}/scripts"
DOCS_DIR="${REPO_ROOT}/docs"

if [[ ! -d "$SCRIPTS_DIR" ]]; then
  echo "ERROR: team/scripts/ not found under repo root: $REPO_ROOT" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Set up output file
# ---------------------------------------------------------------------------
OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"
mkdir -p "$OUTPUT_DIR"

# Epoch at run start (for header timestamp).
RUN_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Tracking: count verdicts for exit code
# ---------------------------------------------------------------------------
MANUAL_REVIEW_COUNT=0
FINDINGS_COUNT=0
PASS_COUNT=0

# ---------------------------------------------------------------------------
# Helper: write a directive section to the output file
#
# Args: $1 = directive tag (e.g. "D01")
#       $2 = short title
#       $3 = verdict string (PASS / FINDINGS / MANUAL-REVIEW-REQUIRED)
#       $4 = body text (multi-line ok)
# ---------------------------------------------------------------------------
write_directive() {
  local tag="$1"
  local title="$2"
  local verdict="$3"
  local body="$4"

  {
    echo ""
    echo "## ${tag} — ${title}"
    echo "Verdict: ${verdict}"
    if [[ -n "$body" ]]; then
      echo ""
      echo "$body"
    fi
  } >> "$OUTPUT_FILE"

  case "$verdict" in
    PASS)                    PASS_COUNT=$(( PASS_COUNT + 1 )) ;;
    FINDINGS)                FINDINGS_COUNT=$(( FINDINGS_COUNT + 1 )) ;;
    MANUAL-REVIEW-REQUIRED)  MANUAL_REVIEW_COUNT=$(( MANUAL_REVIEW_COUNT + 1 )) ;;
  esac

  if [[ "$VERBOSE" == "true" ]]; then
    echo "  ${tag} ${verdict}: ${title}"
    if [[ -n "$body" ]]; then
      echo "$body" | sed 's/^/    /'
    fi
  fi
}

# ---------------------------------------------------------------------------
# Helper: run a Python lint script, capture combined stdout+stderr into a
# named variable, and store the exit code in another named variable.
#
# Usage: run_lint_capture VAR_OUT VAR_EXIT <script_path> [args...]
#
# Sets VAR_OUT to the combined output and VAR_EXIT to the exit code.
# Does not raise a shell error on non-zero exit.
# ---------------------------------------------------------------------------
run_lint_capture() {
  local _out_var="$1"
  local _exit_var="$2"
  local _script_path="$3"
  shift 3
  local _output
  local _rc
  # Capture output and exit code separately.  set +e for this invocation only.
  _output="$(set +e; python3 "$_script_path" "$@" 2>&1; echo "__EXIT__:$?")"
  _rc="${_output##*$'\n'__EXIT__:}"
  _output="${_output%$'\n'__EXIT__:*}"
  printf -v "$_out_var" '%s' "$_output"
  printf -v "$_exit_var" '%s' "$_rc"
}

# ---------------------------------------------------------------------------
# Begin writing the results file
# ---------------------------------------------------------------------------
cat > "$OUTPUT_FILE" <<HEADER
# Coding Standards Audit Data

Run: ${RUN_TIMESTAMP}
Repo root: ${REPO_ROOT}
Script: ${SCRIPTS_DIR}/audit_coding_standards.sh

Each section below corresponds to one directive in docs/coding-standards.md.
Verdict key:
  PASS                   — automated check found no violations
  FINDINGS               — automated check found issues; see body for paths/lines
  MANUAL-REVIEW-REQUIRED — check cannot be fully automated; human judgment required
HEADER

echo "audit_coding_standards: running checks against ${REPO_ROOT}"
echo "  Results file: ${OUTPUT_FILE}"
echo ""

# ===========================================================================
# D01 — Split code by concern; one implementation per operation
# ===========================================================================
# Automated: run lint_lib_function_dedupe.py (detects duplicate function names
# across lib/*.sh).  The broader architectural question of "one impl per
# operation" cannot be checked mechanically beyond duplicate naming.
echo "  [D01] checking for duplicate function names (lint_lib_function_dedupe.py)..."

D01_BODY=""
if [[ -f "${SCRIPTS_DIR}/lint_lib_function_dedupe.py" ]]; then
  D01_OUT=""; D01_EXIT=0
  run_lint_capture D01_OUT D01_EXIT "${SCRIPTS_DIR}/lint_lib_function_dedupe.py"
  if [[ "$D01_EXIT" -ne 0 ]]; then
    D01_BODY="Automated check: lint_lib_function_dedupe.py found duplicate lib/ function names.

${D01_OUT}

Broader architectural compliance (no duplicated operation implementations across
non-lib files) requires manual review of the team/ codebase."
    write_directive "D01" "Split code by concern" "FINDINGS" "$D01_BODY"
  else
    D01_BODY="${D01_OUT}

Broader architectural compliance (one implementation per operation across the
whole tree) requires manual inspection; function deduplication check passed."
    write_directive "D01" "Split code by concern" "MANUAL-REVIEW-REQUIRED" "$D01_BODY"
  fi
else
  write_directive "D01" "Split code by concern" "MANUAL-REVIEW-REQUIRED" \
    "lint_lib_function_dedupe.py not found at ${SCRIPTS_DIR}/lint_lib_function_dedupe.py.
Manual review required: verify no operation has two separate implementations."
fi

# ===========================================================================
# D02 — Keep surfaces thin
# ===========================================================================
# Cannot be checked mechanically: requires understanding whether business logic
# lives in adapters vs. core.  Documented as MANUAL-REVIEW-REQUIRED.
echo "  [D02] surface-thinness check (manual review)..."

write_directive "D02" "Keep surfaces thin" "MANUAL-REVIEW-REQUIRED" \
  "Surface thinness (CLI/dashboard/API adapters must not carry business rules)
cannot be verified mechanically.  Reviewer should confirm:
  - CLI entry points in team/scripts/ call helpers in team/scripts/lib/ rather
    than embedding logic directly.
  - API handlers in team/pgai_agent_kanban/api/routers/ delegate to core modules.
  - Dashboard pane scripts in team/scripts/dashboard/ render only; no logic.
Architecture context: the lib/ structure, pgai_agent_kanban/ package, and
dashboard rendering chain separate concerns at the directory level."

# ===========================================================================
# D03 — Put shared code in a lib/ package and import it; never copy it
# ===========================================================================
# Check: lib/ directories exist and contain shared helpers.  Check Python
# codebase uses imports not copy-paste (heuristic: grep for duplicated function
# def bodies > 5 lines — impractical at scale).  Report as PASS when lib/
# directories present and non-empty; note that copy-paste detection is manual.
echo "  [D03] checking lib/ package existence and non-emptiness..."

D03_ISSUES=()
LIB_BASH="${SCRIPTS_DIR}/lib"
LIB_PY="${TEAM_DIR}/pgai_agent_kanban"

if [[ ! -d "$LIB_BASH" ]]; then
  D03_ISSUES+=("team/scripts/lib/ directory not found")
elif [[ -z "$(ls -A "$LIB_BASH" 2>/dev/null)" ]]; then
  D03_ISSUES+=("team/scripts/lib/ is empty")
else
  D03_LIB_COUNT="$(find "$LIB_BASH" -maxdepth 1 -name '*.sh' | wc -l | tr -d ' ')"
fi

if [[ ! -d "$LIB_PY" ]]; then
  D03_ISSUES+=("team/pgai_agent_kanban/ Python package not found")
fi

if [[ ${#D03_ISSUES[@]} -eq 0 ]]; then
  write_directive "D03" "Put shared code in lib/" "MANUAL-REVIEW-REQUIRED" \
    "Structural check passed: team/scripts/lib/ contains ${D03_LIB_COUNT:-?} .sh helpers;
team/pgai_agent_kanban/ Python package exists.

Copy-paste duplication across non-lib files cannot be detected mechanically.
Manual review should spot-check that non-lib scripts source lib/ functions
rather than reimplementing them inline."
else
  D03_BODY="$(printf '%s\n' "${D03_ISSUES[@]}")"
  write_directive "D03" "Put shared code in lib/" "FINDINGS" "$D03_BODY"
fi

# ===========================================================================
# D04 — Write shared code generic enough for a future surface
# ===========================================================================
# Purely architectural judgment; cannot be automated.
echo "  [D04] shared-code genericity check (manual review)..."

write_directive "D04" "Write generic shared code" "MANUAL-REVIEW-REQUIRED" \
  "Shared-code genericity (designed for reuse by future surfaces, not shaped
around a single caller) is a design property not detectable by static analysis.
Reviewer should inspect team/scripts/lib/ and team/pgai_agent_kanban/ to confirm:
  - Helper functions accept parameters rather than reading globals.
  - No helper function is named after a specific caller.
  - The REST API (team/pgai_agent_kanban/api/) consumes shared logic without
    duplicating it."

# ===========================================================================
# D05 — No hardcoded operational values
# ===========================================================================
# Automated checks:
#   a. lint_env_bootstrap.py: bash scripts must source env_bootstrap.sh before
#      first use of PGAI_AGENT_KANBAN_ROOT_PATH.
#   b. Check that _example companion files exist (kanban.cfg_example etc.).
#   c. Grep for literal /home/ and /opt/ paths in non-test .sh and .py files
#      (heuristic for hardcoded paths).
echo "  [D05] checking hardcoded values (lint_env_bootstrap, example files, path grep)..."

D05_ISSUES=()
D05_NOTES=()

# Sub-check a: lint_env_bootstrap.py
if [[ -f "${SCRIPTS_DIR}/lint_env_bootstrap.py" ]]; then
  D05_BOOTSTRAP_OUT=""; D05_BOOTSTRAP_EXIT=0
  run_lint_capture D05_BOOTSTRAP_OUT D05_BOOTSTRAP_EXIT "${SCRIPTS_DIR}/lint_env_bootstrap.py"
  if [[ "$D05_BOOTSTRAP_EXIT" -ne 0 ]]; then
    D05_ISSUES+=("lint_env_bootstrap.py FINDINGS:")
    while IFS= read -r line; do
      D05_ISSUES+=("  ${line}")
    done <<< "$D05_BOOTSTRAP_OUT"
  else
    D05_NOTES+=("lint_env_bootstrap: OK (bash and python env-bootstrap checks passed)")
  fi
else
  D05_NOTES+=("lint_env_bootstrap.py not found; skipping env-bootstrap check")
fi

# Sub-check b: _example companion files
D05_EXPECTED_EXAMPLES=("kanban.cfg_example" "project.cfg_example" "projects.cfg_example")
for ex in "${D05_EXPECTED_EXAMPLES[@]}"; do
  if [[ ! -f "${REPO_ROOT}/${ex}" ]]; then
    D05_ISSUES+=("Missing config companion: ${ex} (expected at repo root)")
  else
    D05_NOTES+=("Config companion present: ${ex}")
  fi
done

# Sub-check c: grep for hardcoded /home/ paths in scripts and Python files.
# Excludes: example files, test files, comment-only lines, and placeholder
# documentation patterns (youruser, your-user, <user>, BASH_SOURCE references).
D05_HARDCODED=()
while IFS= read -r hit; do
  D05_HARDCODED+=("  ${hit}")
done < <(
  grep -rn --include="*.sh" --include="*.py" \
    -E '/home/[a-zA-Z0-9_]+/' \
    "${SCRIPTS_DIR}" \
    2>/dev/null \
    | grep -v '_example' \
    | grep -v '/tests/' \
    | grep -v 'test_' \
    | grep -v 'youruser' \
    | grep -v 'your-user' \
    | grep -v '<user>' \
    | grep -v 'BASH_SOURCE' \
    | grep -Pv '^\s*#' \
    | head -20 \
    || true
)

if [[ ${#D05_HARDCODED[@]} -gt 0 ]]; then
  D05_ISSUES+=("Potential hardcoded /home/<user>/ paths (verify these are not operational constants):")
  D05_ISSUES+=("${D05_HARDCODED[@]}")
fi

# Assemble body
D05_BODY=""
if [[ ${#D05_NOTES[@]} -gt 0 ]]; then
  D05_BODY+="$(printf '%s\n' "${D05_NOTES[@]}")"
fi
if [[ ${#D05_ISSUES[@]} -gt 0 ]]; then
  if [[ -n "$D05_BODY" ]]; then
    D05_BODY+=$'\n'
  fi
  D05_BODY+="$(printf '%s\n' "${D05_ISSUES[@]}")"
  write_directive "D05" "No hardcoded operational values" "FINDINGS" "$D05_BODY"
else
  write_directive "D05" "No hardcoded operational values" "PASS" "$D05_BODY"
fi

# ===========================================================================
# D06 — Every script and CLI ships a --help
# ===========================================================================
# Automated: lint_help_presence.py (already covers team/scripts/ and cm/).
echo "  [D06] checking --help presence (lint_help_presence.py)..."

if [[ -f "${SCRIPTS_DIR}/lint_help_presence.py" ]]; then
  D06_OUT=""; D06_EXIT=0
  run_lint_capture D06_OUT D06_EXIT "${SCRIPTS_DIR}/lint_help_presence.py"
  if [[ "$D06_EXIT" -ne 0 ]]; then
    write_directive "D06" "Every script ships --help" "FINDINGS" "$D06_OUT"
  else
    write_directive "D06" "Every script ships --help" "PASS" "$D06_OUT"
  fi
else
  write_directive "D06" "Every script ships --help" "FINDINGS" \
    "lint_help_presence.py not found at ${SCRIPTS_DIR}/lint_help_presence.py"
fi

# ===========================================================================
# D07 — Scaffolding and docs ship with the feature in the same RC
# ===========================================================================
# Partially automated: verify docs/ and _example companion files exist.
# Whether ALL features have docs is a manual review question.
echo "  [D07] checking docs and scaffolding presence..."

D07_NOTES=()
D07_ISSUES=()

if [[ -d "$DOCS_DIR" ]]; then
  D07_DOC_COUNT="$(find "$DOCS_DIR" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')"
  D07_NOTES+=("docs/ directory present with ${D07_DOC_COUNT} markdown file(s)")
else
  D07_ISSUES+=("docs/ directory not found at ${DOCS_DIR}")
fi

# Check for the three expected _example companion files
for ex in "${D05_EXPECTED_EXAMPLES[@]}"; do
  if [[ ! -f "${REPO_ROOT}/${ex}" ]]; then
    D07_ISSUES+=("Missing scaffolding companion: ${ex}")
  fi
done

# Check for per-flavor docker-compose examples (containerized deployment scaffolding)
if [[ -f "${REPO_ROOT}/docker/debian/docker-compose.example.yaml" ]]; then
  D07_NOTES+=("docker/debian/docker-compose.example.yaml present")
else
  D07_NOTES+=("docker/debian/docker-compose.example.yaml not found (may be intentional)")
fi
if [[ -f "${REPO_ROOT}/docker/rhel9/docker-compose.example.yaml" ]]; then
  D07_NOTES+=("docker/rhel9/docker-compose.example.yaml present")
else
  D07_NOTES+=("docker/rhel9/docker-compose.example.yaml not found (may be intentional)")
fi

D07_BODY="$(printf '%s\n' "${D07_NOTES[@]}")"
if [[ ${#D07_ISSUES[@]} -gt 0 ]]; then
  D07_BODY+=$'\n'"$(printf '%s\n' "${D07_ISSUES[@]}")"
  write_directive "D07" "Scaffolding and docs ship with feature" "FINDINGS" \
    "${D07_BODY}

Whether every feature in this RC has matching docs requires manual review
of the RC diff and release notes."
else
  write_directive "D07" "Scaffolding and docs ship with feature" "MANUAL-REVIEW-REQUIRED" \
    "${D07_BODY}

Structural scaffolding files present.  Whether every new feature in this RC
shipped with its documentation requires manual review of the RC diff."
fi

# ===========================================================================
# D08 — Comments describe behavior, never process history
# ===========================================================================
# Automated: lint_comment_provenance.py.
echo "  [D08] checking comment provenance (lint_comment_provenance.py)..."

if [[ -f "${SCRIPTS_DIR}/lint_comment_provenance.py" ]]; then
  D08_OUT=""; D08_EXIT=0
  run_lint_capture D08_OUT D08_EXIT "${SCRIPTS_DIR}/lint_comment_provenance.py"
  if [[ "$D08_EXIT" -ne 0 ]]; then
    write_directive "D08" "Comments describe behavior" "FINDINGS" "$D08_OUT"
  else
    write_directive "D08" "Comments describe behavior" "PASS" "$D08_OUT"
  fi
else
  write_directive "D08" "Comments describe behavior" "FINDINGS" \
    "lint_comment_provenance.py not found at ${SCRIPTS_DIR}/lint_comment_provenance.py"
fi

# ===========================================================================
# D09 — REST API serves Swagger/OpenAPI and versioned contract file
# ===========================================================================
# Automated: check that the FastAPI app.py references /docs (Swagger auto-gen)
# and that an ICD_VERSION file (or equivalent contract file) exists.
echo "  [D09] checking REST API Swagger/OpenAPI setup..."

D09_NOTES=()
D09_ISSUES=()

API_APP_PY="${TEAM_DIR}/pgai_agent_kanban/api/app.py"
if [[ -f "$API_APP_PY" ]]; then
  # FastAPI auto-generates /docs by default; confirm it is not disabled.
  if grep -q 'docs_url' "$API_APP_PY" 2>/dev/null; then
    if grep -q 'docs_url=None' "$API_APP_PY" 2>/dev/null; then
      D09_ISSUES+=("FastAPI docs_url=None found in api/app.py — Swagger UI appears disabled")
    else
      D09_NOTES+=("api/app.py: FastAPI docs_url configured (Swagger UI enabled)")
    fi
  else
    # docs_url absent → FastAPI default is /docs (enabled).
    D09_NOTES+=("api/app.py: FastAPI default Swagger UI at /docs (docs_url not overridden)")
  fi

  # Check api/app.py documents /docs in its module docstring.
  if grep -q 'GET /docs' "$API_APP_PY" 2>/dev/null; then
    D09_NOTES+=("api/app.py: /docs route documented in module docstring")
  else
    D09_NOTES+=("api/app.py: /docs not explicitly mentioned in module docstring (FastAPI still serves it by default)")
  fi
else
  D09_ISSUES+=("api/app.py not found at ${API_APP_PY} — REST API may not exist")
fi

# Check for versioned contract file (ICD_VERSION or openapi*.yaml/json).
ICD_VERSION_FILE="${TEAM_DIR}/pgai_agent_kanban/api/ICD_VERSION"
OPENAPI_FILES=()
while IFS= read -r f; do
  OPENAPI_FILES+=("$f")
done < <(find "${TEAM_DIR}" -maxdepth 5 \
  \( -name 'openapi*.yaml' -o -name 'openapi*.json' \) \
  2>/dev/null || true)

if [[ -f "$ICD_VERSION_FILE" ]]; then
  ICD_VER_CONTENT="$(cat "$ICD_VERSION_FILE" | tr -d '\n')"
  D09_NOTES+=("ICD_VERSION file present: ${ICD_VER_CONTENT}")
elif [[ ${#OPENAPI_FILES[@]} -gt 0 ]]; then
  D09_NOTES+=("OpenAPI contract file(s) found: ${OPENAPI_FILES[*]}")
else
  D09_ISSUES+=("No ICD_VERSION or openapi contract file found under team/")
fi

D09_BODY="$(printf '%s\n' "${D09_NOTES[@]}")"
if [[ ${#D09_ISSUES[@]} -gt 0 ]]; then
  D09_BODY+=$'\n'"$(printf '%s\n' "${D09_ISSUES[@]}")"
  write_directive "D09" "REST API serves Swagger and versioned contract" "FINDINGS" "$D09_BODY"
else
  write_directive "D09" "REST API serves Swagger and versioned contract" "PASS" "$D09_BODY"
fi

# ===========================================================================
# D10 — Default stack: Python 3.12+ for logic, bash for orchestration
# ===========================================================================
# Automated: check Python version requirement; check for non-standard language
# files (Ruby, Go, JavaScript, etc.) under team/.
echo "  [D10] checking default language stack..."

D10_NOTES=()
D10_ISSUES=()

# Check Python version in pyproject.toml or requirements.txt
PYPROJECT="${TEAM_DIR}/pyproject.toml"
if [[ -f "$PYPROJECT" ]]; then
  if grep -q 'python_requires' "$PYPROJECT" 2>/dev/null \
     || grep -q 'python =' "$PYPROJECT" 2>/dev/null \
     || grep -q 'requires-python' "$PYPROJECT" 2>/dev/null; then
    PY_REQ="$(grep -E '(python_requires|python =|requires-python)' "$PYPROJECT" | head -1 | tr -d ' ')"
    D10_NOTES+=("Python version constraint in pyproject.toml: ${PY_REQ}")
  else
    D10_NOTES+=("pyproject.toml present but no explicit Python version constraint found")
  fi
else
  D10_NOTES+=("pyproject.toml not found at ${PYPROJECT}")
fi

# Check for non-standard language files under team/ (JS, Ruby, Go, Rust, Java)
NON_STANDARD=()
while IFS= read -r f; do
  NON_STANDARD+=("$f")
done < <(
  find "${TEAM_DIR}" \
    \( -name '*.rb' -o -name '*.js' -o -name '*.ts' -o -name '*.go' \
       -o -name '*.rs' -o -name '*.java' -o -name '*.kt' \) \
    -not -path '*/node_modules/*' \
    -not -path '*/.git/*' \
    2>/dev/null | head -20 || true
)

if [[ ${#NON_STANDARD[@]} -gt 0 ]]; then
  D10_ISSUES+=("Non-standard language files found under team/ (review for compliance):")
  for f in "${NON_STANDARD[@]}"; do
    D10_ISSUES+=("  ${f}")
  done
else
  D10_NOTES+=("No non-standard language files (Ruby, JS, Go, Rust, Java) found under team/")
fi

D10_BODY="$(printf '%s\n' "${D10_NOTES[@]}")"
if [[ ${#D10_ISSUES[@]} -gt 0 ]]; then
  D10_BODY+=$'\n'"$(printf '%s\n' "${D10_ISSUES[@]}")"
  write_directive "D10" "Default stack: Python 3.12+ and bash" "FINDINGS" "$D10_BODY"
else
  write_directive "D10" "Default stack: Python 3.12+ and bash" "MANUAL-REVIEW-REQUIRED" \
    "${D10_BODY}

Automated check found no non-standard language files.  Manual verification:
confirm no logic-heavy scripts use awk/perl beyond one-liners, and that any
authorized deviation from the default stack is documented in the relevant ticket."
fi

# ===========================================================================
# D11 — Docker and compose artifacts; fail-loud entrypoint
# ===========================================================================
# Automated: check Dockerfile and docker-compose.example.yaml exist; check
# entrypoint.sh uses set -e or equivalent fail-loud pattern.
echo "  [D11] checking Docker artifacts and fail-loud entrypoint..."

D11_NOTES=()
D11_ISSUES=()

DOCKERFILE_DEBIAN="${REPO_ROOT}/docker/debian/Dockerfile"
DOCKERFILE_RHEL9="${REPO_ROOT}/docker/rhel9/Dockerfile"
COMPOSE_EXAMPLE_DEBIAN="${REPO_ROOT}/docker/debian/docker-compose.example.yaml"
COMPOSE_EXAMPLE_RHEL9="${REPO_ROOT}/docker/rhel9/docker-compose.example.yaml"
ENTRYPOINT="${REPO_ROOT}/docker/entrypoint.sh"

if [[ -f "$DOCKERFILE_DEBIAN" ]]; then
  D11_NOTES+=("Dockerfile present: docker/debian/Dockerfile")
else
  D11_ISSUES+=("Dockerfile not found at docker/debian/Dockerfile")
fi

if [[ -f "$DOCKERFILE_RHEL9" ]]; then
  D11_NOTES+=("Dockerfile present: docker/rhel9/Dockerfile")
else
  D11_ISSUES+=("Dockerfile not found at docker/rhel9/Dockerfile")
fi

if [[ -f "$COMPOSE_EXAMPLE_DEBIAN" ]]; then
  D11_NOTES+=("docker-compose example present: docker/debian/docker-compose.example.yaml")
else
  D11_ISSUES+=("docker-compose.example.yaml not found at docker/debian/docker-compose.example.yaml")
fi

if [[ -f "$COMPOSE_EXAMPLE_RHEL9" ]]; then
  D11_NOTES+=("docker-compose example present: docker/rhel9/docker-compose.example.yaml")
else
  D11_ISSUES+=("docker-compose.example.yaml not found at docker/rhel9/docker-compose.example.yaml")
fi

if [[ -f "$ENTRYPOINT" ]]; then
  # Check for fail-loud pattern: set -e, set -euo pipefail, or exec at PID 1.
  if grep -qE '^\s*set\s+-[^-]*(e)' "$ENTRYPOINT" 2>/dev/null; then
    D11_NOTES+=("docker/entrypoint.sh: uses set -e (fail-loud)")
  elif grep -qE '^\s*exec\s+' "$ENTRYPOINT" 2>/dev/null; then
    D11_NOTES+=("docker/entrypoint.sh: uses exec for PID-1 replacement (fail-loud)")
  else
    D11_ISSUES+=("docker/entrypoint.sh: no set -e or exec found — verify fail-loud pattern")
  fi

  # Check for bind-mount validation (entrypoints must verify required mounts).
  if grep -qE '(exit [1-9]|err|fail|missing|not found|required)' "$ENTRYPOINT" 2>/dev/null; then
    D11_NOTES+=("docker/entrypoint.sh: startup validation present (error exits detected)")
  else
    D11_ISSUES+=("docker/entrypoint.sh: no startup validation detected — entrypoint may swallow failures")
  fi
else
  D11_ISSUES+=("docker/entrypoint.sh not found")
fi

D11_BODY="$(printf '%s\n' "${D11_NOTES[@]}")"
if [[ ${#D11_ISSUES[@]} -gt 0 ]]; then
  D11_BODY+=$'\n'"$(printf '%s\n' "${D11_ISSUES[@]}")"
  write_directive "D11" "Docker artifacts and fail-loud entrypoint" "FINDINGS" "$D11_BODY"
else
  write_directive "D11" "Docker artifacts and fail-loud entrypoint" "PASS" "$D11_BODY"
fi

# ===========================================================================
# Footer and summary
# ===========================================================================
{
  echo ""
  echo "---"
  echo ""
  echo "## Audit Summary"
  echo ""
  echo "Run: ${RUN_TIMESTAMP}"
  echo "Total directives: 11"
  echo "PASS: ${PASS_COUNT}"
  echo "FINDINGS: ${FINDINGS_COUNT}"
  echo "MANUAL-REVIEW-REQUIRED: ${MANUAL_REVIEW_COUNT}"
} >> "$OUTPUT_FILE"

echo ""
echo "audit_coding_standards: complete."
echo "  PASS:                   ${PASS_COUNT}"
echo "  FINDINGS:               ${FINDINGS_COUNT}"
echo "  MANUAL-REVIEW-REQUIRED: ${MANUAL_REVIEW_COUNT}"
echo ""
echo "Results written to: ${OUTPUT_FILE}"
echo ""

# Exit 0 when no MANUAL-REVIEW-REQUIRED or FINDINGS; exit 1 otherwise.
if [[ $MANUAL_REVIEW_COUNT -gt 0 || $FINDINGS_COUNT -gt 0 ]]; then
  exit 1
fi

exit 0
