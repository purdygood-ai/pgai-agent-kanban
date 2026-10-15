#!/usr/bin/env bash
# team/scripts/release-locks.sh
# CLI verb: list every advisory-flock lock file under the kanban root's
# locks/ directory with its provenance and a held/stale/unparseable
# verdict, or release one named lock.
#
# Usage:
#   release-locks.sh                          List every lock (read-only):
#                                              its held line ("held: <dur>
#                                              (since <acquired_at>) by
#                                              <holder> pid <pid> — <task>",
#                                              from the recorded acquired_at,
#                                              never the mtime), a held
#                                              verdict against the stale
#                                              threshold, its provenance,
#                                              and its liveness verdict.
#   release-locks.sh --release NAME           Release the named lock.
#   release-locks.sh --release NAME --force   Signal the live holder (its
#                                              recorded pid) so it releases
#                                              the lock itself, and wait for
#                                              it to.
#   release-locks.sh --help, -h               Show help and exit.
#
# Thin adapter: parses the flags via operator_args.sh, then reaches
# pgai_agent_kanban.ops.locks through pp_run_ops -- list_locks for the
# listing, release_lock for --release -- the same two functions
# GET /release-locks and POST /operations/release-locks call in process.
# That module's docstring carries the full behaviour; in short:
#
#   NAME resolution: exact match against a lock's name (its lock-file
#   basename with the trailing ".lock" stripped, which is also ignored on
#   the typed name), then an unambiguous hyphen-boundary prefix. Zero or
#   several matches refuse dual-channel rather than guessing.
#
#   Verdict: "held" when the recorded pid answers a signal-0 probe, "stale"
#   when it does not, "unparseable" when the record carries no pid. Every
#   *.lock file is listed, whatever its verdict.
#
#   Dead holder: the lock's flock is probed first; when it is free the
#   lock file is atomically replaced with an empty one (the kernel already
#   dropped the dead holder's flock). When it is still held -- by a process
#   that inherited the holder's lock descriptor, or one in another pid
#   namespace -- the release is refused (release_locks.orphan_holder,
#   exit 4) naming the pids holding the file open; with --force those pids
#   are sent SIGTERM and the file is replaced once the flock comes free.
#   No provenance: reported as already released, so a re-run is a no-op.
#
#   Live holder: refused (release_locks.holder_alive, exit 4) naming the
#   holder's holder, pid, op and task; the file is left in place. With
#   --force the record is re-read first: if the lock changed hands since it
#   was read (the holder let go and another chain took it), nobody is
#   signalled. Otherwise the holder is sent SIGTERM and this waits up to
#   twice kill_grace_seconds ([wake] in kanban.cfg, or the
#   KILL_GRACE_SECONDS override; default 30) plus 10 seconds for its own
#   exit path to release the lock -- a stopping wake first stops its
#   provider's process group, then releases its lock. The
#   lock counts as released only once no process holds the flock on the
#   lock file's original inode -- a child that inherited the holder's lock
#   descriptor keeps it held after the holder exits. A live holder's file is
#   never replaced or removed.
#
# Read-only by default: with no --release flag (or an empty one), this
# script only reads $KANBAN_ROOT/locks/*.lock files.
#
# Exit codes:
#   0  listing printed, or release succeeded (including a no-op re-release
#      of a lock that was already gone)
#   1  usage error
#   2  --release name matches more than one lock (candidates named)
#   3  --release name matches no lock
#   4  --release target's recorded holder pid is alive, or its recorded
#      pid is dead while another process still holds its flock, and --force
#      was not given
#   5  --release --force signalled the live holder (or the processes
#      holding a dead holder's flock), but the lock is still held after
#      twice kill_grace_seconds plus 10 seconds (by the holder, or by a
#      process that inherited its lock descriptor), or nobody holding it
#      could be signalled at all

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_SCRIPT_DIR}/lib"

# Source shared libraries.
# shellcheck source=lib/operator_args.sh
source "${_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/script_common.sh
source "${_LIB_DIR}/script_common.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_LIB_DIR}/pp_run_ops.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# release-locks accepts: --release --force --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(release force help)

# --- Parse arguments (--release is outside the canonical operator_args.sh
#     value-flag vocabulary, so it is added for this parse only; --force
#     and --help are already canonical booleans) ---
operator_args_parse --extra-value-flags "release" "$@"

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_USAGE_TEXT="$(operator_args_render_help_for_flags \
    "$(basename "$0")" \
    "List every advisory lock under the kanban root's locks/ directory with its provenance, or release one named lock." \
    OPERATOR_VALID_FLAGS \
    "" \
    "         value: a lock's name (its lock-file basename with the" \
    "                trailing .lock stripped), or an unambiguous prefix of" \
    "                one. Ambiguous or unmatched names refuse rather than" \
    "                guessing." \
    "  (--force overrides the refusal to release a lock whose recorded" \
    "   holder pid is still alive: the holder is sent SIGTERM so its own" \
    "   exit path releases the lock, and this script waits up to twice" \
    "   kill_grace_seconds plus 10 seconds for it to; a lock that changed" \
    "   hands since it was read is reported let go and nobody is" \
    "   signalled. The live holder's file is never replaced or removed." \
    "   Ignored when --release is not given.)" \
    "" \
    "Behavior:" \
    "  No flags:        list every lock file with how long it has been" \
    "                   held (from its recorded acquired_at), its" \
    "                   provenance, and a held/stale/unparseable verdict" \
    "                   (read-only)." \
    "  --release NAME:  release the named lock. A dead holder's file is" \
    "                   swapped for an empty one once its flock is free." \
    "                   Refuses if the recorded holder pid is alive, or" \
    "                   if it is dead but another process still holds the" \
    "                   flock, unless --force is given, in which case the" \
    "                   holder (or the processes holding the flock) is" \
    "                   signalled (see above)." \
    "" \
    "Exit codes:" \
    "  0  listing printed, or release succeeded (a re-release of an" \
    "     already-released lock also exits 0)" \
    "  1  usage error" \
    "  2  --release name matches more than one lock (candidates named)" \
    "  3  --release name matches no lock" \
    "  4  --release target's holder pid is alive (or dead while another" \
    "     process holds its flock) and --force was not given" \
    "  5  --release --force signalled the live holder, but the lock is still" \
    "     held after twice kill_grace_seconds plus 10 seconds (by the holder," \
    "     or by a process that inherited its lock descriptor), or it could" \
    "     not be signalled")"

if argparse_has help; then
    print_usage_and_exit 0 "$_USAGE_TEXT"
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# release-locks is not project-scoped -- locks/ is a single kanban-root-wide
# directory shared by every project's wake, repo, and OVERWATCH
# acquisitions -- so, unlike most operator verbs, it takes no --project.
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

_release_value="$(operator_args_get release)"

# Exit codes pass through: 2 ambiguous, 3 not found, 4 live holder, 5 live
# holder not released.
_rc=0
if [[ -z "${_release_value}" ]]; then
    pp_run_ops pgai_agent_kanban.ops list_locks --kanban-root="${KANBAN_ROOT}" || _rc=$?
else
    _ops_args=(--kanban-root="${KANBAN_ROOT}" --name="${_release_value}")
    if argparse_has force; then _ops_args+=(--force); fi
    pp_run_ops pgai_agent_kanban.ops release_lock "${_ops_args[@]}" || _rc=$?
fi
exit "${_rc}"
