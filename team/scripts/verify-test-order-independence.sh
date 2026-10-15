#!/usr/bin/env bash
# team/scripts/verify-test-order-independence.sh
#
# On-demand order-coupling self-test for the unit test estate. Runs the same
# collected test set twice -- once in pytest's normal collection order, once
# in a deliberately different order -- and diffs the per-test PASS/FAIL/SKIP
# outcomes. Any test whose outcome differs between the two runs is order-
# coupled: it depends on process-global state (environment, cwd, imported
# module state, a singleton) left behind by whatever ran before it, rather
# than on its own fixtures.
#
# This is a DEEP-VERIFY-style on-demand check, not a fast per-RC gate: it
# runs the whole collected set twice, so its wall-clock cost is roughly
# double a single run-unit-tests.sh invocation (see the timing this script
# prints at the end). Wiring a second full pass into the fast per-RC gate
# would double every RC's suite cost for a class of bug this project has
# not observed in practice -- see "Criteria for the isolated set" below for
# what would justify escalating a specific suite instead of paying that
# cost universally. Run this script manually, or from a periodic/operator
# job, the same way deep-verify.sh's cold-build lane is invoked on demand
# rather than per RC (team/SOP.md "The DEEP-VERIFY tier").
#
# Mechanism: the alternate order is produced by tests/conftest.py's
# pytest_collection_modifyitems hook (PGAI_TEST_ORDER=reverse), which is
# pure pytest + stdlib -- no third-party ordering plugin is a dependency of
# this check. That hook canonicalises the collected list (sorts it by
# nodeid) before reversing it, so "reverse" here means the reverse of the
# canonical nodeid order, not a literal reversal of whatever order pytest's
# own collection happened to produce -- see tests/conftest.py's
# PGAI_TEST_ORDER documentation block. Both passes are also invoked with
# `-p no:randomly`, which NEUTRALISES pytest-randomly when it is active in
# the environment (pytest-randomly reorders collected items before
# conftest.py's own hook runs); the hook's own canonicalisation makes the
# reversed pass order-plugin-independent regardless, but the flag is kept
# so the forward pass reflects pytest's own default collection order
# rather than pytest-randomly's per-run shuffle. The flag is harmless when
# the plugin is absent: pytest accepts `-p no:<name>` for a plugin name it
# has never heard of as a no-op, which is the case on the container hosts
# built by the three-host matrix (they install only requirements-test.txt,
# which does not declare pytest-randomly).
#
# Criteria for the isolated set
# ------------------------------
# A suite belongs in "run in its own process" territory when THIS script
# (or an equivalent forward-vs-reversed comparison) demonstrates a verdict
# difference attributable to it -- not on suspicion. Concretely: this
# script's own diff names a test whose PASS/FAIL differs between the two
# orders, AND isolating the responsible module (running it alone) reproduces
# the SAME verdict as one of the two full-estate orders, confirming it is
# the leaking module rather than a downstream victim. Until a suite clears
# that bar, it stays in the shared process -- adding isolation on suspicion
# alone costs every future run without evidence it fixes anything (see
# roles/CODER.md "Scope Adherence": resist the temptation to isolate
# broadly as insurance).
#
# If a future run of this script finds a genuine divergence, the fix belongs
# in run-unit-tests.sh / run-integration-tests.sh: move the offending file
# out of the shared PYTEST_ARGS glob (via --ignore=<file>) and invoke it in
# its own separate `python3 -m pytest <file>` subprocess call, so its
# process-global state can never leak into (or be corrupted by) the rest of
# the estate. That is what "run in their own process" means operationally --
# this script's job is only to prove WHICH suites need it, with evidence.
#
# Usage:
#   team/scripts/verify-test-order-independence.sh [--target unit|integration|both]
#                                                    [--verbose] [--help]
#
# Exit codes:
#   0  No verdict differences found between the two orders.
#   1  At least one test's verdict differed between the two orders.
#   2  Configuration or environment error (pytest missing, etc).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/dev_tree.sh
source "${_SCRIPT_DIR}/lib/dev_tree.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/lib/temp.sh"

_VOI_SELF_ROOT="$(cd "${_SCRIPT_DIR}/../.." && pwd)"
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree 2>/dev/null || true)}"
if [[ -z "${PGAI_DEV_TREE_PATH:-}" && -d "${_VOI_SELF_ROOT}" ]]; then
    export PGAI_DEV_TREE_PATH="${_VOI_SELF_ROOT}"
fi
unset _VOI_SELF_ROOT

TEAM_DIR="${_SCRIPT_DIR}/.."

TARGET="unit"
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            [[ $# -ge 2 ]] || { echo "ERROR: --target requires a value (unit|integration|both)" >&2; exit 2; }
            TARGET="$2"
            shift 2
            ;;
        --target=*)
            TARGET="${1#--target=}"
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [--target unit|integration|both] [--verbose]

Runs the collected pytest estate twice (forward, then reversed collection
order via PGAI_TEST_ORDER=reverse) and diffs per-test outcomes. Any
divergence is a named order-coupling finding. Both passes run with
-p no:randomly, neutralising pytest-randomly (or any other third-party
ordering plugin registered under that name) when it is present, so the
two orders compared are the ones their labels claim.

Options:
  --target <unit|integration|both>  Which suite(s) to check (default: unit)
  --verbose, -v                     Pass --verbose to the underlying pytest runs
  --help, -h                        Show this help and exit

Exit codes:
  0  No verdict differences between orders.
  1  At least one order-coupling finding.
  2  Configuration or environment error.
EOF
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

case "$TARGET" in
    unit|integration|both) ;;
    *) echo "ERROR: --target must be one of: unit, integration, both" >&2; exit 2 ;;
esac

if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
    echo "ERROR: pytest is not installed in this Python environment." >&2
    exit 2
fi

_VOI_WORKDIR="$(pgai_mktemp_d verify_order_independence)"
cleanup_workdir() {
    local exit_code=$?
    rm -rf "$_VOI_WORKDIR" 2>/dev/null || true
    exit "$exit_code"
}
trap cleanup_workdir EXIT

declare -a _VOI_TARGETS=()
[[ "$TARGET" == "unit" || "$TARGET" == "both" ]] && _VOI_TARGETS+=("unit:tests/unit/ pgai_agent_kanban/api/tests/")
[[ "$TARGET" == "integration" || "$TARGET" == "both" ]] && _VOI_TARGETS+=("integration:tests/integration/")

echo "==================================================================="
echo "  verify-test-order-independence.sh"
echo "  targets: $TARGET"
echo "==================================================================="
echo ""

_VOI_OVERALL_EXIT=0

_voi_run() {
    # $1 = label (forward|reverse), $2 = space-separated pytest path args,
    # $3 = output junit-xml path, $4 = PGAI_TEST_ORDER value ("" for forward)
    local label="$1" paths="$2" junit_path="$3" order="$4"
    local extra_verbose=()
    [[ "$VERBOSE" == "true" ]] && extra_verbose=("--verbose")
    # Each pass gets its own temp root, and pytest's basetemp sits INSIDE
    # it. Subprocesses spawned by the tests (the git boundary guard shim in
    # particular) read PGAI_AGENT_KANBAN_TEMP_DIR to decide what is
    # throwaway space: a fixture that pushes into a bare repository under
    # its own tmp_path is exempt only when that tmp_path is under the temp
    # root the guard sees. A basetemp outside that root makes the guard
    # refuse every fixture-local push under a non-CM role, so those tests
    # fail identically in both orders and the diff never evaluates them.
    # pytest creates the basetemp itself but not its parent, hence mkdir.
    # Living under _VOI_WORKDIR also lets the EXIT trap clean it up.
    local pass_temp_root="$_VOI_WORKDIR/kanban_tmp_${label}"
    local basetemp="${pass_temp_root}/pytest_basetemp"
    mkdir -p "$pass_temp_root"
    (
        cd "$TEAM_DIR"
        # shellcheck disable=SC2086
        PGAI_TEST_ORDER="$order" \
        PGAI_AGENT_KANBAN_TEMP_DIR="$pass_temp_root" \
            python3 -m pytest -p no:randomly $paths -q "${extra_verbose[@]}" \
                --basetemp="$basetemp" \
                --junitxml="$junit_path" \
                >"${_VOI_WORKDIR}/${label}.log" 2>&1
    ) || true
}

_voi_diff_junit() {
    # Diffs two junit-xml files' per-testcase outcome and prints any
    # divergence. Returns 0 if identical, 1 if any test's outcome differs.
    python3 - "$1" "$2" <<'PYEOF'
import sys
import xml.etree.ElementTree as ET

def outcomes(path):
    tree = ET.parse(path)
    root = tree.getroot()
    result = {}
    for tc in root.iter("testcase"):
        node_id = f'{tc.get("classname")}::{tc.get("name")}'
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "FAILED"
        elif tc.find("skipped") is not None:
            status = "SKIPPED"
        else:
            status = "PASSED"
        result[node_id] = status
    return result

forward = outcomes(sys.argv[1])
reversed_ = outcomes(sys.argv[2])

all_ids = sorted(set(forward) | set(reversed_))
diffs = []
for node_id in all_ids:
    f = forward.get(node_id, "MISSING")
    r = reversed_.get(node_id, "MISSING")
    if f != r:
        diffs.append((node_id, f, r))

print(f"Forward order:  {len(forward)} tests collected")
print(f"Reversed order: {len(reversed_)} tests collected")

if diffs:
    print(f"\nORDER-COUPLING FINDINGS ({len(diffs)}):")
    for node_id, f, r in diffs:
        print(f"  {node_id}: forward={f} reversed={r}")
    sys.exit(1)

print("\nNo verdict differences between forward and reversed collection order.")
sys.exit(0)
PYEOF
}

for entry in "${_VOI_TARGETS[@]}"; do
    label="${entry%%:*}"
    paths="${entry#*:}"

    echo "--- ${label}: forward (normal collection order) ---"
    forward_junit="${_VOI_WORKDIR}/${label}_forward.xml"
    start_forward=$(date +%s)
    _voi_run "${label}_forward" "$paths" "$forward_junit" ""
    end_forward=$(date +%s)
    echo "    completed in $((end_forward - start_forward))s"

    echo "--- ${label}: reversed collection order (PGAI_TEST_ORDER=reverse) ---"
    reverse_junit="${_VOI_WORKDIR}/${label}_reverse.xml"
    start_reverse=$(date +%s)
    _voi_run "${label}_reverse" "$paths" "$reverse_junit" "reverse"
    end_reverse=$(date +%s)
    echo "    completed in $((end_reverse - start_reverse))s"

    if [[ ! -f "$forward_junit" || ! -f "$reverse_junit" ]]; then
        echo "ERROR: ${label}: one or both pytest runs did not produce a junit-xml report." >&2
        echo "       forward log:  ${_VOI_WORKDIR}/${label}_forward.log" >&2
        echo "       reverse log:  ${_VOI_WORKDIR}/${label}_reverse.log" >&2
        _VOI_OVERALL_EXIT=2
        continue
    fi

    echo ""
    echo "--- ${label}: diff ---"
    if ! _voi_diff_junit "$forward_junit" "$reverse_junit"; then
        _VOI_OVERALL_EXIT=1
    fi
    echo ""
done

echo "==================================================================="
if [[ "$_VOI_OVERALL_EXIT" -eq 0 ]]; then
    echo "  VERDICT: PASS -- no order-coupling findings"
else
    echo "  VERDICT: FAIL -- see findings above"
fi
echo "==================================================================="
exit "$_VOI_OVERALL_EXIT"
