#!/usr/bin/env bash
# team/scripts/verify-rc-state.sh
# Read-only consistency checker for kanban project RC state.
#
# Usage:
#   verify-rc-state.sh <project> [--verbose]
#
# Arguments:
#   <project>    project name; must match a directory under projects/<name>/
#   --verbose    print every check (OK, WARN, ERROR); by default only WARN/ERROR
#                and the final summary line are printed
#
# Checks performed:
#   a. release-state.md consistency:
#      - Active RC is `none` OR matching rc/<version> branch exists in dev tree
#      - Last Released matches most-recent git tag
#      - All timestamps are valid ISO-8601
#   b. Queue <-> folder consistency:
#      - Every non-[x] task ID in *_backlog.md has a matching task folder on disk
#      - Every task folder has a matching queue entry
#   c. status.md state <-> queue marker consistency:
#      - [ ] = BACKLOG, [W] = WAITING, [A] = WORKING, [B] = BLOCKED,
#        [x] = DONE or WONT-DO
#   d. Cross-task Prerequisites references resolve:
#      - Every ID in a task README's ## Prerequisites section resolves to a folder
#   e. release-state.md <-> git branch consistency:
#      - Active RC: vX.Y.Z  → rc/vX.Y.Z must exist locally
#      - Active RC: none     → no rc/* branches should exist (WARN, not ERROR)
#   f. Bundle invariants:
#      - Every PRIORITY/BUG file referenced in a requirements bundle exists
#      - Every PRIORITY/BUG marked [x] in its backlog corresponds to a bundled
#        requirements file
#
# Output format:
#   [OK]    <message>
#   [WARN]  <message>
#   [ERROR] <message>
#   verify-rc-state: N errors, N warnings, N ok
#
# Exit codes:
#   0   no ERRORs found (WARN findings do not affect exit code)
#   1   one or more ERROR findings
#   2   pre-flight failure (bad arguments, project not found, etc.)
#
# Configuration:
#   KANBAN_ROOT           — path to the kanban live install root
#                           (default: $HOME/pgai_agent_kanban)
#   PGAI_DEV_TREE_PATH    — path to the dev tree git clone
#                           (derived from project config when not set)

# ---------------------------------------------------------------------------
# Argument parsing (before strict mode for cleaner error output)
# ---------------------------------------------------------------------------
PROJECT_ARG=""
VERBOSE=0

_usage() {
    echo "Usage: $(basename "$0") <project> [--verbose]" >&2
    echo "" >&2
    echo "  <project>    project name (must exist under projects/<name>/)" >&2
    echo "  --verbose    print every check, not just WARN/ERROR" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose)
            VERBOSE=1
            shift
            ;;
        --*)
            echo "ERROR: unknown flag: $1" >&2
            echo "" >&2
            _usage
            exit 2
            ;;
        *)
            if [[ -z "$PROJECT_ARG" ]]; then
                PROJECT_ARG="$1"
            else
                echo "ERROR: unexpected positional argument: $1" >&2
                echo "" >&2
                _usage
                exit 2
            fi
            shift
            ;;
    esac
done

if [[ -z "$PROJECT_ARG" ]]; then
    echo "ERROR: <project> is required." >&2
    echo "" >&2
    _usage
    exit 2
fi

# ---------------------------------------------------------------------------
# Resolve script / library paths (BEFORE strict mode)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve KANBAN_ROOT
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

# Source optional env / config files (they may use unset vars — pre-strict)
[[ -f "$KANBAN_ROOT/bashrc" ]]               && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]]                  && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]]     && source "$HOME/.config/pgai-kanban.cfg"

# Source INI parser (needed by project_paths.sh)
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/lib/ini_parser.sh"

# Source kanban.cfg for PGAI_DEV_TREE_PATH when not already set
TEAM_ROOT="$KANBAN_ROOT"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$TEAM_ROOT/kanban.cfg" paths dev_tree_path "")}"
fi

# Source project path helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"

# Source semver helpers
# shellcheck source=lib/semver.sh
source "${SCRIPT_DIR}/lib/semver.sh"

# ---------------------------------------------------------------------------
# Enable strict mode for our own code
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve project paths
# ---------------------------------------------------------------------------
PROJECT_NAME="$(pp_require_project_context "$PROJECT_ARG")" || {
    echo "ERROR: could not resolve project context for '$PROJECT_ARG'." >&2
    exit 2
}

PROJECT_ROOT="$(pp_project_root "$PROJECT_NAME")" || {
    echo "ERROR: could not resolve project root for '$PROJECT_NAME'." >&2
    exit 2
}

if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "ERROR: project directory not found: $PROJECT_ROOT" >&2
    exit 2
fi

# Load project config to get the dev tree path
pp_load_config "$PROJECT_NAME" || {
    echo "ERROR: could not load project config for '$PROJECT_NAME'" >&2
    exit 2
}
REPO_ROOT="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
if [[ -z "$REPO_ROOT" ]]; then
    echo "ERROR: dev_tree_path is not set in project config for '$PROJECT_NAME'" >&2
    exit 2
fi

RELEASE_STATE="$(pp_release_state "$PROJECT_NAME")"
TASKS_DIR="$(pp_tasks_dir "$PROJECT_NAME")"
REQUIREMENTS_DIR="$(pp_requirements_dir "$PROJECT_NAME")"
PRIORITY_DIR="$(pp_priority_dir "$PROJECT_NAME")"
BUGS_DIR="$(pp_bugs_dir "$PROJECT_NAME")"

# ---------------------------------------------------------------------------
# Counters and output helpers
# ---------------------------------------------------------------------------
_COUNT_OK=0
_COUNT_WARN=0
_COUNT_ERROR=0

_ok() {
    _COUNT_OK=$(( _COUNT_OK + 1 ))
    if [[ "$VERBOSE" -eq 1 ]]; then
        printf '[OK]    %s\n' "$1"
    fi
}

_warn() {
    _COUNT_WARN=$(( _COUNT_WARN + 1 ))
    printf '[WARN]  %s\n' "$1"
}

_error() {
    _COUNT_ERROR=$(( _COUNT_ERROR + 1 ))
    printf '[ERROR] %s\n' "$1"
}

# ---------------------------------------------------------------------------
# Helper: read a named field from a markdown file (single invocation)
# Usage: _read_md_field <file> <heading>
# ---------------------------------------------------------------------------
_read_md_field() {
    local file="$1"
    local heading="$2"
    python3 - "$file" "$heading" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
heading = sys.argv[2]
lines = text.splitlines()
for i, line in enumerate(lines):
    if line.strip() == f"## {heading}":
        for follow in lines[i+1:]:
            v = follow.strip()
            if v and not v.startswith("#"):
                print(v)
                raise SystemExit(0)
        break
print("none")
PY
}

# ---------------------------------------------------------------------------
# Helper: validate ISO-8601 timestamp
# Accepts: YYYY-MM-DDThh:mm:ss[+/-hh:mm] or YYYY-MM-DDThh:mm:ssZ or "none"
# ---------------------------------------------------------------------------
_is_valid_iso8601() {
    local ts="$1"
    [[ "$ts" == "none" ]] && return 0
    if [[ "$ts" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([+-][0-9]{2}:[0-9]{2}|Z)?$ ]]; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Helper: get all queue backlog file paths for a project
# Outputs one path per line.
# ---------------------------------------------------------------------------
_get_queue_files() {
    local flat_dir="$TASKS_DIR/queues"
    local -a result=()

    for agent in coder cm pm tester writer bug priority; do
        local flat_f="$flat_dir/${agent}_backlog.md"
        if [[ -f "$flat_f" ]]; then
            result+=("$flat_f")
        fi
    done

    printf '%s\n' "${result[@]}"
}

# ---------------------------------------------------------------------------
# CHECK A: release-state.md consistency
# ---------------------------------------------------------------------------
_check_a() {
    echo ""
    echo "--- Check a: release-state.md consistency ---"

    if [[ ! -f "$RELEASE_STATE" ]]; then
        _error "release-state.md not found: $RELEASE_STATE"
        return
    fi
    _ok "release-state.md exists: $RELEASE_STATE"

    local active_rc last_released rc_opened_at last_released_at
    active_rc="$(_read_md_field "$RELEASE_STATE" "Active RC")"
    last_released="$(_read_md_field "$RELEASE_STATE" "Last Released")"
    rc_opened_at="$(_read_md_field "$RELEASE_STATE" "RC Opened At")"
    last_released_at="$(_read_md_field "$RELEASE_STATE" "Last Released At")"

    # Active RC: must be "none" or a valid semver
    if [[ "$active_rc" == "none" ]]; then
        _ok "Active RC is 'none' (no RC in flight)"
    elif [[ "$active_rc" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        _ok "Active RC has valid format: $active_rc"
    else
        _error "release-state.md: Active RC has invalid format: '$active_rc' (expected 'none' or vX.Y.Z) — file: $RELEASE_STATE"
    fi

    # RC Opened At timestamp
    if _is_valid_iso8601 "$rc_opened_at"; then
        _ok "RC Opened At timestamp valid: $rc_opened_at"
    else
        _error "release-state.md: RC Opened At is not valid ISO-8601: '$rc_opened_at' — file: $RELEASE_STATE"
    fi

    # Last Released At timestamp
    if _is_valid_iso8601 "$last_released_at"; then
        _ok "Last Released At timestamp valid: $last_released_at"
    else
        _error "release-state.md: Last Released At is not valid ISO-8601: '$last_released_at' — file: $RELEASE_STATE"
    fi

    # Last Released: must be "none" or a valid semver
    if [[ "$last_released" == "none" ]]; then
        _ok "Last Released is 'none' (no prior release)"
    elif [[ "$last_released" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        _ok "Last Released has valid format: $last_released"

        # Compare Last Released with most-recent git tag merged into origin/main
        local latest_tag
        latest_tag="$(pp_last_released_version "$PROJECT_NAME" 2>/dev/null || echo "v0.0.0")"

        if [[ "$latest_tag" == "v0.0.0" ]]; then
            _warn "release-state.md: could not determine latest git tag to validate Last Released '$last_released' — git may be unavailable or no tags exist on origin/main"
        elif semver_eq "$last_released" "$latest_tag"; then
            _ok "Last Released matches most-recent git tag: $last_released"
        else
            _warn "release-state.md: Last Released '$last_released' does not match most-recent git tag '$latest_tag' — file: $RELEASE_STATE (may be intentional if tag not yet squash-merged to origin/main)"
        fi
    else
        _error "release-state.md: Last Released has invalid format: '$last_released' (expected 'none' or vX.Y.Z) — file: $RELEASE_STATE"
    fi
}

# ---------------------------------------------------------------------------
# CHECK B: Queue <-> folder consistency
# Uses a single batched Python call to scan all queue files and task folders.
# ---------------------------------------------------------------------------
_check_b() {
    echo ""
    echo "--- Check b: Queue <-> folder consistency ---"

    if [[ ! -d "$TASKS_DIR" ]]; then
        _warn "Tasks directory not found: $TASKS_DIR — skipping queue/folder checks"
        return
    fi

    # Collect queue file paths
    local -a queue_files=()
    while IFS= read -r qf; do
        [[ -n "$qf" ]] && queue_files+=("$qf")
    done < <(_get_queue_files)

    if [[ "${#queue_files[@]}" -eq 0 ]]; then
        _warn "No queue backlog files found under $TASKS_DIR/queues — skipping queue/folder checks"
        return
    fi

    # Export tasks dir and queue files to python
    local qfiles_joined
    qfiles_joined="$(printf '%s\n' "${queue_files[@]}")"

    # Single Python call: scan queue files + task folders; emit findings as TSV
    # Format: <severity>\t<message>
    while IFS=$'\t' read -r severity msg; do
        case "$severity" in
            OK)    _ok "$msg" ;;
            WARN)  _warn "$msg" ;;
            ERROR) _error "$msg" ;;
        esac
    done < <(python3 - "$TASKS_DIR" <<PY
import os
import pathlib
import re
import sys

tasks_dir = pathlib.Path(sys.argv[1])
queue_files_raw = """${qfiles_joined}"""

queue_files = [p for p in queue_files_raw.strip().splitlines() if p]

# ---------------------------------------------------------------
# Parse all queue files: build id -> (marker, qfile_name) maps
# ---------------------------------------------------------------
all_queued = {}   # task_id -> (marker, qfile_name)

line_pat = re.compile(r'^\s*-\s+\[([^\]]*)\]\s+(\S+)')

for qf in queue_files:
    qpath = pathlib.Path(qf)
    if not qpath.is_file():
        continue
    qname = qpath.name
    for line in qpath.read_text().splitlines():
        m = line_pat.match(line)
        if m:
            marker = m.group(1)
            tid = m.group(2)
            all_queued[tid] = (marker, qname)

# ---------------------------------------------------------------
# Open queue entries: non-[x]
# ---------------------------------------------------------------
open_queued = {
    tid: (marker, qname)
    for tid, (marker, qname) in all_queued.items()
    if marker != 'x'
}

# ---------------------------------------------------------------
# Task ID pattern: OWNER-ROLE-YYYYMMDD-NNN-slug
# ---------------------------------------------------------------
task_id_pat = re.compile(r'^[A-Z]+-[A-Z]+-\d{8}-\d+')

# ---------------------------------------------------------------
# Check 1: every open queue entry has a folder on disk
# Only CLAUDE-style task IDs (OWNER-ROLE-YYYYMMDD-NNN) live under tasks/.
# PRIORITY-NNNN and BUG-NNNN identifiers belong in their own namespaces
# (priority/ and bugs/) — skip them here to avoid false-positive ERRORs.
# ---------------------------------------------------------------
ok1 = 0
for tid, (marker, qname) in sorted(open_queued.items()):
    if not task_id_pat.match(tid):
        continue
    folder = tasks_dir / tid
    if folder.is_dir():
        ok1 += 1
    else:
        print(f"ERROR\tQueue entry has no folder on disk: {tid} — referenced in {qname} but {folder} does not exist")

if ok1:
    print(f"OK\tQueue->folder: {ok1} open entries have matching folders")

# ---------------------------------------------------------------
# Check 2: every task folder has a queue entry
# ---------------------------------------------------------------
ok2 = 0
for d in sorted(tasks_dir.iterdir()):
    if not d.is_dir():
        continue
    dname = d.name
    if dname in ('queues', 'README.md'):
        continue
    if not task_id_pat.match(dname):
        continue

    if dname in all_queued:
        ok2 += 1
    else:
        # Check state from status.md
        status_file = d / 'status.md'
        if status_file.is_file():
            text = status_file.read_text()
            state = 'unknown'
            in_state = False
            for line in text.splitlines():
                if line.strip() == '## State':
                    in_state = True
                    continue
                if in_state:
                    v = line.strip()
                    if v and not v.startswith('#'):
                        state = v
                        break
            if state in ('DONE', 'WONT-DO'):
                ok2 += 1
            else:
                print(f"WARN\tTask folder {dname} (state: {state}) has no matching queue entry — tasks/queues may be out of sync")
        else:
            print(f"WARN\tTask folder {dname} has no status.md and no queue entry — orphaned task folder")

if ok2:
    print(f"OK\tFolder->queue: {ok2} task folders accounted for")
PY
)
}

# ---------------------------------------------------------------------------
# CHECK C: status.md state <-> queue marker consistency
# Uses a single batched Python call.
# ---------------------------------------------------------------------------
_check_c() {
    echo ""
    echo "--- Check c: status.md state <-> queue marker consistency ---"

    if [[ ! -d "$TASKS_DIR" ]]; then
        _warn "Tasks directory not found: $TASKS_DIR — skipping state/marker checks"
        return
    fi

    local -a queue_files=()
    while IFS= read -r qf; do
        [[ -n "$qf" ]] && queue_files+=("$qf")
    done < <(_get_queue_files)

    if [[ "${#queue_files[@]}" -eq 0 ]]; then
        _warn "No queue backlog files found — skipping state/marker checks"
        return
    fi

    local qfiles_joined
    qfiles_joined="$(printf '%s\n' "${queue_files[@]}")"

    while IFS=$'\t' read -r severity msg; do
        case "$severity" in
            OK)    _ok "$msg" ;;
            WARN)  _warn "$msg" ;;
            ERROR) _error "$msg" ;;
        esac
    done < <(python3 - "$TASKS_DIR" <<PY
import pathlib
import re
import sys

tasks_dir = pathlib.Path(sys.argv[1])
queue_files_raw = """${qfiles_joined}"""
queue_files = [p for p in queue_files_raw.strip().splitlines() if p]

line_pat = re.compile(r'^\s*-\s+\[([^\]]*)\]\s+(\S+)')
task_id_pat = re.compile(r'^[A-Z]+-[A-Z]+-\d{8}-\d+')

# Build id -> (marker, qfile_name)
id_to_marker = {}
id_to_qfile  = {}
for qf in queue_files:
    qpath = pathlib.Path(qf)
    if not qpath.is_file():
        continue
    qname = qpath.name
    for line in qpath.read_text().splitlines():
        m = line_pat.match(line)
        if m:
            marker = m.group(1)
            tid = m.group(2)
            id_to_marker[tid] = marker
            id_to_qfile[tid]  = qname

def read_state(status_file):
    text = pathlib.Path(status_file).read_text()
    in_state = False
    for line in text.splitlines():
        if line.strip() == '## State':
            in_state = True
            continue
        if in_state:
            v = line.strip()
            if v and not v.startswith('#'):
                return v
    return 'none'

# Expected marker per state
EXPECTED = {
    'BACKLOG':  ' ',
    'WAITING':  'W',
    'WORKING':  'A',
    'BLOCKED':  ('B', ' ', 'A'),  # B preferred; legacy may have ' ' or 'A'
    'DONE':     'x',
    'WONT-DO':  'x',
}

c_ok = 0
c_err = 0
c_warn = 0

for d in sorted(tasks_dir.iterdir()):
    if not d.is_dir():
        continue
    dname = d.name
    if not task_id_pat.match(dname):
        continue
    if dname not in id_to_marker:
        continue

    status_file = d / 'status.md'
    if not status_file.is_file():
        continue

    state = read_state(status_file)
    marker = id_to_marker[dname]
    qref   = id_to_qfile[dname]

    if state == 'none':
        print(f"WARN\tstatus/marker: {dname} has no ## State field in {status_file}")
        c_warn += 1
        continue

    if state not in EXPECTED:
        print(f"WARN\tstatus/marker: {dname} has unknown state '{state}' in {status_file}")
        c_warn += 1
        continue

    expected = EXPECTED[state]
    if isinstance(expected, tuple):
        # BLOCKED: B preferred; warn on legacy
        if marker not in expected:
            print(f"ERROR\tstatus/marker mismatch: {dname} state={state} but queue marker=[{marker}] in {qref} (expected [B] or legacy [ ]/[A]) — status: {status_file}")
            c_err += 1
        elif marker in (' ', 'A'):
            print(f"WARN\tstatus/marker: {dname} state=BLOCKED has marker=[{marker}] in {qref} (prefer [B])")
            c_warn += 1
        else:
            c_ok += 1
    else:
        if marker == expected:
            c_ok += 1
        else:
            exp_display = f"[{expected}]" if expected != ' ' else "[ ]"
            print(f"ERROR\tstatus/marker mismatch: {dname} state={state} but queue marker=[{marker}] in {qref} (expected {exp_display}) — status: {status_file}")
            c_err += 1

if c_ok:
    print(f"OK\tState/marker: {c_ok} task(s) consistent")
PY
)
}

# ---------------------------------------------------------------------------
# CHECK D: Cross-task Prerequisites references resolve
# Uses a single batched Python call over all task READMEs.
# ---------------------------------------------------------------------------
_check_d() {
    echo ""
    echo "--- Check d: Prerequisites references ---"

    if [[ ! -d "$TASKS_DIR" ]]; then
        _warn "Tasks directory not found: $TASKS_DIR — skipping prerequisites checks"
        return
    fi

    while IFS=$'\t' read -r severity msg; do
        case "$severity" in
            OK)    _ok "$msg" ;;
            WARN)  _warn "$msg" ;;
            ERROR) _error "$msg" ;;
        esac
    done < <(python3 - "$TASKS_DIR" <<'PY'
import pathlib
import re
import sys

tasks_dir = pathlib.Path(sys.argv[1])
task_id_pat = re.compile(r'^[A-Z]+-[A-Z]+-\d{8}-\d+')

# Collect all task folder names
task_folders = {
    d.name
    for d in tasks_dir.iterdir()
    if d.is_dir() and task_id_pat.match(d.name)
}

# Pattern to extract task IDs from prerequisite lines
prereq_id_pat = re.compile(r'\b(CLAUDE-[A-Z]+-\d{8}-\d+-\S+)')

d_ok = 0
d_err = 0

for d in sorted(tasks_dir.iterdir()):
    if not d.is_dir():
        continue
    dname = d.name
    if not task_id_pat.match(dname):
        continue

    readme = d / 'README.md'
    if not readme.is_file():
        continue

    text = readme.read_text()
    in_prereqs = False
    for line in text.splitlines():
        if line.strip() == '## Prerequisites':
            in_prereqs = True
            continue
        if in_prereqs:
            if line.startswith('## '):
                break
            # Strip trailing punctuation
            line_clean = line.strip().rstrip('.,')
            for m in prereq_id_pat.finditer(line_clean):
                prereq_id = m.group(1).rstrip('.,')
                if prereq_id in task_folders:
                    d_ok += 1
                else:
                    print(f"ERROR\tPrerequisites: dangling reference in {dname} README — '{prereq_id}' has no task folder at {tasks_dir / prereq_id}")
                    d_err += 1

if d_ok:
    print(f"OK\tPrerequisites: {d_ok} reference(s) resolved")
PY
)
}

# ---------------------------------------------------------------------------
# CHECK E: release-state.md <-> git branch consistency
# ---------------------------------------------------------------------------
_check_e() {
    echo ""
    echo "--- Check e: release-state.md <-> git branch consistency ---"

    if [[ ! -f "$RELEASE_STATE" ]]; then
        _warn "release-state.md not found — skipping git branch consistency checks"
        return
    fi

    if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        _warn "Dev tree at $REPO_ROOT is not a git repository — skipping git branch checks"
        return
    fi

    local active_rc
    active_rc="$(_read_md_field "$RELEASE_STATE" "Active RC")"

    # Get all local rc/* branches
    local -a rc_branches=()
    while IFS= read -r branch; do
        branch="${branch#  }"
        branch="${branch# }"
        branch="${branch#\* }"
        [[ -n "$branch" ]] && rc_branches+=("$branch")
    done < <(git -C "$REPO_ROOT" branch --list 'rc/*' 2>/dev/null || true)

    if [[ "$active_rc" == "none" ]]; then
        if [[ "${#rc_branches[@]}" -eq 0 ]]; then
            _ok "Active RC is none and no local rc/* branches exist"
        else
            for br in "${rc_branches[@]}"; do
                _warn "release-state.md shows Active RC=none but local branch exists: $br — may be operator-staged or orphaned work"
            done
        fi
    else
        local rc_branch="rc/${active_rc}"
        if git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$rc_branch" >/dev/null 2>&1; then
            _ok "Active RC $active_rc has matching local branch: $rc_branch"
        else
            _error "release-state.md Active RC=$active_rc but local branch $rc_branch does not exist in $REPO_ROOT"
        fi

        for br in "${rc_branches[@]}"; do
            if [[ "$br" != "$rc_branch" ]]; then
                _warn "Extra rc/* branch found: $br (Active RC is $active_rc) — may be a stale or orphaned branch in $REPO_ROOT"
            fi
        done
    fi
}

# ---------------------------------------------------------------------------
# CHECK F: Bundle invariants
# Uses a single batched Python call.
# ---------------------------------------------------------------------------
_check_f() {
    echo ""
    echo "--- Check f: Bundle invariants ---"

    if [[ ! -d "$REQUIREMENTS_DIR" ]]; then
        _warn "Requirements directory not found: $REQUIREMENTS_DIR — skipping bundle invariant checks"
        return
    fi

    # Collect active (non-SUPERSEDED) requirements files
    local -a req_files=()
    for f in "$REQUIREMENTS_DIR"/*.md; do
        [[ -f "$f" ]] || continue
        [[ "$(basename "$f")" == "README.md" ]] && continue
        [[ "$f" == *SUPERSEDED* ]] && continue
        req_files+=("$f")
    done

    if [[ "${#req_files[@]}" -eq 0 ]]; then
        _ok "No active requirements files found (nothing to validate)"
        return
    fi

    local priority_backlog bug_backlog
    priority_backlog="$(pp_queue_path "$PROJECT_NAME" "priority" 2>/dev/null || true)"
    bug_backlog="$(pp_queue_path "$PROJECT_NAME" "bug" 2>/dev/null || true)"

    local req_files_joined
    req_files_joined="$(printf '%s\n' "${req_files[@]}")"

    while IFS=$'\t' read -r severity msg; do
        case "$severity" in
            OK)    _ok "$msg" ;;
            WARN)  _warn "$msg" ;;
            ERROR) _error "$msg" ;;
        esac
    done < <(python3 - "$PRIORITY_DIR" "$BUGS_DIR" "${priority_backlog:-}" "${bug_backlog:-}" <<PY
import pathlib
import re
import sys

priority_dir  = pathlib.Path(sys.argv[1])
bugs_dir      = pathlib.Path(sys.argv[2])
priority_bl   = sys.argv[3]
bug_bl        = sys.argv[4]

req_files_raw = """${req_files_joined}"""
req_files = [p for p in req_files_raw.strip().splitlines() if p]

# Build a pattern to parse ## Bundled Items sections
ref_pat = re.compile(r'\b((?:PRIORITY|BUG)-\d+[^\s\`)"]*)')

def extract_bundled_ids(req_file):
    """Return deduplicated list of IDs from ## Bundled Items section.
    Each bullet line contains the ID twice (once as filename, once in path).
    We take only the first match per line to avoid duplicate findings.
    """
    text = pathlib.Path(req_file).read_text()
    ids = []
    seen = set()
    in_section = False
    for line in text.splitlines():
        if line.strip() == '## Bundled Items':
            in_section = True
            continue
        if in_section:
            if line.startswith('## '):
                break
            m = ref_pat.search(line)
            if m:
                ident = m.group(1)
                if ident.endswith('.md'):
                    ident = ident[:-3]
                if ident not in seen:
                    seen.add(ident)
                    ids.append(ident)
    return ids

# ---------------------------------------------------------------
# F1: every referenced PRIORITY/BUG file exists on disk
# ---------------------------------------------------------------
f_ok = 0
f_err = 0

for req_file in req_files:
    req_base = pathlib.Path(req_file).name
    for ref_id in extract_bundled_ids(req_file):
        if ref_id.startswith('PRIORITY-'):
            ref_dir = priority_dir
        elif ref_id.startswith('BUG-'):
            ref_dir = bugs_dir
        else:
            continue

        found = False
        if ref_dir.is_dir():
            for candidate in ref_dir.glob(ref_id + '*.md'):
                found = True
                print(f"OK\tBundle ref exists: {ref_id} -> {candidate} (in {req_base})")
                f_ok += 1
                break
        if not found:
            print(f"ERROR\tBundle invariant: {req_base} references {ref_id} but no matching file found in {ref_dir}")
            f_err += 1

# ---------------------------------------------------------------
# F2: every PRIORITY/BUG marked [x] in backlog is in some bundle
# ---------------------------------------------------------------

# Collect all IDs bundled across all active req files
bundled_priority = set()
bundled_bug      = set()
for req_file in req_files:
    for ref_id in extract_bundled_ids(req_file):
        if ref_id.startswith('PRIORITY-'):
            bundled_priority.add(ref_id)
        elif ref_id.startswith('BUG-'):
            bundled_bug.add(ref_id)

line_pat = re.compile(r'^\s*-\s+\[([^\]]*)\]\s+(\S+)')

def check_backlog_x(backlog_path, bundled_set, kind):
    if not backlog_path or not pathlib.Path(backlog_path).is_file():
        return
    ok_n = warn_n = 0
    for line in pathlib.Path(backlog_path).read_text().splitlines():
        m = line_pat.match(line)
        if m and m.group(1) == 'x':
            tid = m.group(2)
            if not tid.startswith(kind + '-'):
                continue
            if tid in bundled_set:
                ok_n += 1
            else:
                print(f"WARN\t{kind} backlog [x]: {tid} is marked done but not found in any active requirements file — may have been bundled into a superseded RC or already shipped")
                warn_n += 1
    if ok_n:
        print(f"OK\t{kind} backlog: {ok_n} [x] entries accounted for in active bundles")

check_backlog_x(priority_bl, bundled_priority, 'PRIORITY')
check_backlog_x(bug_bl, bundled_bug, 'BUG')

if f_ok:
    print(f"OK\tBundle invariants: {f_ok} reference(s) validated")
PY
)
}

# ---------------------------------------------------------------------------
# MAIN: run all checks
# ---------------------------------------------------------------------------
echo "verify-rc-state.sh — project: $PROJECT_NAME"
echo "Kanban root:  $KANBAN_ROOT"
echo "Project root: $PROJECT_ROOT"
echo "Dev tree:     $REPO_ROOT"

_check_a
_check_b
_check_c
_check_d
_check_e
_check_f

# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------
echo ""
echo "verify-rc-state: ${_COUNT_ERROR} errors, ${_COUNT_WARN} warnings, ${_COUNT_OK} ok"

if [[ "${_COUNT_ERROR}" -gt 0 ]]; then
    exit 1
fi

exit 0
