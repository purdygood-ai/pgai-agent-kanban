#!/usr/bin/env bash
# team/scripts/harness/dashboard-idle-budget.sh
#
# Idle-CPU budget harness for the tmux dashboard: builds the real dashboard
# (team/scripts/dashboard/create.sh, from this checkout) on a PRIVATE tmux
# socket (`tmux -L <name>`) against a throwaway fixture kanban root, leaves
# it detached with nobody switching windows, and measures the summed CPU
# time of the dashboard's whole process tree -- the private tmux server and
# every descendant: pane shells, cadence loops, renderers -- from /proc over
# the measurement window. Other work on the host is not counted, because
# only that one process tree is read.
#
# The result is reported as a percentage of one core and compared with the
# budget (default 25%).
#
# ===========================================================================
# ENFORCEMENT CLASS
# ===========================================================================
# CONVERT (quality class, team/SOP-core.md "Blocking Severity: only real
# damage blocks"): an over-budget reading is a performance finding, not
# evidence anything is broken. The harness narrates one verdict line and
# ALWAYS exits 0 -- over budget, under budget, or unable to measure. It is
# never wired into a gated runner.
#
# ===========================================================================
# USAGE
# ===========================================================================
#   dashboard-idle-budget.sh [--duration SECONDS] [--settle SECONDS]
#                            [--budget-pct PERCENT] [--keep]
#
#   --duration SECONDS  Length of the measurement window (default 600 --
#                       ten minutes).
#   --settle SECONDS    Time to wait after the session exists before the
#                       window opens, so the panes' one-time arrival renders
#                       are not counted as idle cost (default 30).
#   --budget-pct N      Budget as a percentage of one core (default 25).
#   --keep              Leave the throwaway root in place for inspection
#                       (the tmux server is always killed).
#   --help, -h          Print this help and exit 0.
#
# Output (stdout): progress lines, then one verdict line of the form
#   dashboard-idle-budget: verdict=<UNDER|OVER|NOT-MEASURED> cpu_pct=<X>
#   cpu_seconds=<S> window_seconds=<D> budget_pct=<B> processes=<N>
#
# Requirements: tmux, /proc. Without either the verdict is NOT-MEASURED,
# naming the missing resource, and the exit status is still 0.
#
# ===========================================================================
# HOW THE TREE'S CPU IS SUMMED
# ===========================================================================
# For every process in the tree, /proc/<pid>/stat gives utime+stime (its own
# CPU) and cutime+cstime (CPU of children it has already reaped). A render
# that starts and exits inside the window is reaped by its pane loop, so its
# CPU moves into that loop's cutime/cstime. The tree total is therefore the
# sum of all four fields over every live process in the tree; the value at
# the end of the window minus the value at its start is the CPU the
# dashboard spent during the window.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TEAM_DIR="${REPO_ROOT}/team"

# shellcheck source=lib/throwaway-root.sh
source "${SCRIPT_DIR}/lib/throwaway-root.sh"

DURATION=600
SETTLE=30
BUDGET_PCT=25
KEEP=false

_usage() {
  grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed -n '2,48p'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration) DURATION="${2:-}"; shift 2 ;;
    --duration=*) DURATION="${1#--duration=}"; shift ;;
    --settle) SETTLE="${2:-}"; shift 2 ;;
    --settle=*) SETTLE="${1#--settle=}"; shift ;;
    --budget-pct) BUDGET_PCT="${2:-}"; shift 2 ;;
    --budget-pct=*) BUDGET_PCT="${1#--budget-pct=}"; shift ;;
    --keep) KEEP=true; shift ;;
    --help|-h) _usage; exit 0 ;;
    *)
      # Operator-CLI argument dispatch for a quality-class harness: a
      # usage error is narrated and the run ends without measuring.
      echo "dashboard-idle-budget.sh: unknown argument: $1 (see --help)" >&2
      exit 2
      ;;
  esac
done

for _v in DURATION SETTLE BUDGET_PCT; do
  if [[ ! "${!_v}" =~ ^[0-9]+$ ]]; then
    echo "dashboard-idle-budget.sh: --$(tr '[:upper:]_' '[:lower:]-' <<<"${_v}") must be a whole number, got '${!_v}'" >&2
    exit 2
  fi
done
unset _v

_verdict() {
  # _verdict <verdict> <key=value ...>
  local verdict="$1"
  shift
  printf 'dashboard-idle-budget: verdict=%s %s\n' "$verdict" "$*"
}

if ! command -v tmux >/dev/null 2>&1; then
  _verdict NOT-MEASURED "reason=tmux-not-on-PATH"
  exit 0
fi
if [[ ! -r /proc/self/stat ]]; then
  _verdict NOT-MEASURED "reason=/proc-not-readable"
  exit 0
fi

CLK_TCK="$(getconf CLK_TCK 2>/dev/null || echo 100)"
REAL_TMUX="$(command -v tmux)"
SOCKET="pgai_idle_budget_$$"
SESSION="pgai-idle-budget-$$"

RUN_ROOT="$(harness_new_throwaway_root "dashboard-idle-budget")"
KROOT="${RUN_ROOT}/kanban-root"
SHIM_DIR="${RUN_ROOT}/tmux-shim"
mkdir -p "${KROOT}/tmp" "${KROOT}/logs" "${SHIM_DIR}"

_cleanup() {
  "${REAL_TMUX}" -L "${SOCKET}" kill-server >/dev/null 2>&1 || true
  if [[ "$KEEP" != "true" ]]; then
    rm -rf "${RUN_ROOT}" 2>/dev/null || true
  fi
}
trap _cleanup EXIT

# --- Fixture kanban root: one registered release-workflow project with
# empty queues -- enough for every window create.sh builds to render. ---
_build_fixture_root() {
  local project="demo"
  local proot="${KROOT}/projects/${project}"
  mkdir -p "${proot}/tasks/queues" "${proot}/bugs" "${proot}/priority" \
    "${proot}/requirements" "${proot}/artifacts"
  local agent
  for agent in pm coder writer tester cm po; do
    printf '# %s backlog\n' "$agent" > "${proot}/tasks/queues/${agent}_backlog.md"
  done
  printf '[project:%s]\npriority = 1\n' "$project" > "${KROOT}/projects.cfg"
  printf '[project]\nname = %s\nworkflow_type = release\n' "$project" > "${proot}/project.cfg"
  printf '[paths]\ndev_tree_path = %s\n' "${REPO_ROOT}" > "${KROOT}/kanban.cfg"
  : > "${KROOT}/logs/cron-pm.log"
}
_build_fixture_root

cat > "${SHIM_DIR}/tmux" <<EOF
#!/usr/bin/env bash
exec "${REAL_TMUX}" -L "${SOCKET}" "\$@"
EOF
chmod +x "${SHIM_DIR}/tmux"

echo "dashboard-idle-budget: fixture root ${KROOT}; private tmux socket ${SOCKET}"

# --- Build the dashboard detached. create.sh ends in `exec tmux
# attach-session`, which fails without a terminal once the session is
# fully built; that one expected failure is ignored and success is judged
# by the session existing. Every inherited name that points at a live
# install is cleared or repointed together. ---
(
  env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PGAI_PROJECT_NAME -u TMUX -u TMUX_PANE \
    PATH="${SHIM_DIR}:${PATH}" \
    KANBAN_ROOT="${KROOT}" \
    PGAI_AGENT_KANBAN_ROOT_PATH="${KROOT}" \
    PGAI_AGENT_KANBAN_TEMP_DIR="${KROOT}/tmp" \
    PGAI_DEV_TREE_PATH="${REPO_ROOT}" \
    PGAI_DASHBOARD_SESSION_NAME="${SESSION}" \
    bash "${TEAM_DIR}/scripts/dashboard/create.sh" --kanban-root "${KROOT}" --session "${SESSION}" \
    </dev/null >"${RUN_ROOT}/create.log" 2>&1
) || true

if ! "${REAL_TMUX}" -L "${SOCKET}" has-session -t "${SESSION}" 2>/dev/null; then
  _verdict NOT-MEASURED "reason=dashboard-session-did-not-start log=${RUN_ROOT}/create.log"
  KEEP=true
  exit 0
fi

SERVER_PID="$("${REAL_TMUX}" -L "${SOCKET}" display-message -p -t "${SESSION}" '#{pid}' 2>/dev/null || true)"
if [[ ! "${SERVER_PID}" =~ ^[0-9]+$ ]]; then
  _verdict NOT-MEASURED "reason=tmux-server-pid-unresolved"
  exit 0
fi
ATTACHED="$("${REAL_TMUX}" -L "${SOCKET}" display-message -p -t "${SESSION}" '#{session_attached}' 2>/dev/null || echo '?')"
echo "dashboard-idle-budget: session ${SESSION} up (tmux server pid ${SERVER_PID}, attached clients ${ATTACHED}); settling ${SETTLE}s"

# _tree_ticks <root-pid>: echoes "<total-ticks> <process-count>" for the
# root and every descendant, summing utime+stime+cutime+cstime.
_tree_ticks() {
  local root="$1" f line pid rest
  for f in /proc/[0-9]*/stat; do
    pid="${f#/proc/}"
    pid="${pid%/stat}"
    line=""
    { read -r line < "$f"; } 2>/dev/null || continue
    rest="${line##*) }"
    printf '%s %s\n' "$pid" "$rest"
  done | awk -v root="$root" '
    {
      pid = $1
      ppid[pid] = $3
      ticks[pid] = $13 + $14 + $15 + $16
    }
    END {
      in_tree[root] = 1
      changed = 1
      while (changed) {
        changed = 0
        for (p in ppid) {
          if (!(p in in_tree) && (ppid[p] in in_tree)) { in_tree[p] = 1; changed = 1 }
        }
      }
      total = 0; n = 0
      for (p in in_tree) { if (p in ticks) { total += ticks[p]; n++ } }
      printf "%d %d\n", total, n
    }'
}

sleep "${SETTLE}"
read -r START_TICKS START_N < <(_tree_ticks "${SERVER_PID}")
START_EPOCH="$(date +%s)"
echo "dashboard-idle-budget: measuring for ${DURATION}s (${START_N} processes in the tree at start)"
sleep "${DURATION}"
read -r END_TICKS END_N < <(_tree_ticks "${SERVER_PID}")
END_EPOCH="$(date +%s)"

WINDOW=$(( END_EPOCH - START_EPOCH ))
[[ "${WINDOW}" -le 0 ]] && WINDOW=1
DELTA=$(( END_TICKS - START_TICKS ))
[[ "${DELTA}" -lt 0 ]] && DELTA=0

read -r CPU_SECONDS CPU_PCT VERDICT < <(awk -v d="${DELTA}" -v hz="${CLK_TCK}" -v w="${WINDOW}" -v b="${BUDGET_PCT}" '
  BEGIN {
    s = d / hz
    pct = 100 * s / w
    printf "%.2f %.2f %s\n", s, pct, (pct < b ? "UNDER" : "OVER")
  }')

_verdict "${VERDICT}" "cpu_pct=${CPU_PCT} cpu_seconds=${CPU_SECONDS} window_seconds=${WINDOW} budget_pct=${BUDGET_PCT} processes=${END_N}"
exit 0
