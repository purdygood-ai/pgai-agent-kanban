#!/usr/bin/env bash
# team/scripts/lib/lock_provenance.sh
#
# Shared acquire/release pair for every advisory-flock lock file this
# framework holds. Before this file existed, each acquisition site opened
# an empty file, took a non-blocking flock on it, and wrote nothing —  a
# held lock named no holder, no pid, no operation, and no task, so a
# contended caller could say only that somebody holds it. A wedged lock
# could not be diagnosed and a stale one could not be told from a live one.
#
# pgai_lock_acquire makes writing that record part of taking the lock: an
# acquisition that cannot name all four of who (holder), which process
# (pid), doing what (op), and on behalf of which task (task) is refused
# rather than silently granted. pgai_lock_release clears the record as
# part of dropping the lock.
#
# SOURCE THIS FILE; do not execute it directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/lock_provenance.sh"
#
# This file self-sources its one dependency (error_contract.sh) at source
# time -- a cheap, side-effect-free function redefinition, mirroring the
# idiom wake_task_transition.sh already uses -- so pgai_lock_acquire works
# whether this file is sourced through the full wake/OVERWATCH pipeline
# (which already sources error_contract.sh) or on its own, as unit tests do.
#
# Functions defined here:
#   pgai_lock_acquire <lock_file> <holder> <pid> <op> <task> <out_fd_var>
#   pgai_lock_acquire_waiting <wait_seconds> <lock_file> <holder> <pid> <op> <task> <out_fd_var>
#   pgai_lock_wait_now
#   pgai_lock_wait_pause <seconds>
#   pgai_lock_release <lock_file> <fd>
#   pgai_lock_release_all_held
#   pgai_lock_read_provenance <lock_file>
#   pgai_lock_describe_provenance <content>
#   pgai_lock_field <content> <key>
#   pgai_lock_pid_liveness_verdict <content>
#   pgai_proc_start_time <pid>
#   pgai_boot_id
#   pgai_lock_format_age_seconds <seconds>
#   pgai_lock_files_for_project <project>
#   pgai_lock_chain_budget_floor_seconds
#   pgai_lock_stale_age_threshold_seconds [project]
#   pgai_lock_stale_age_threshold_derivation [project]
#   pgai_lock_contention_probe_count <lock_file>
#   pgai_lock_still_held <lock_file> <expected_pid>
#   pgai_lock_reverify_or_narrate <lock_file> <expected_pid> <phase> <op> <task>
#   pgai_lock_abandon <lock_file> <fd>
#   pgai_lock_refresh_held_mtime <lock_file> <fd>
#   pgai_lock_set_task <lock_file> <fd> <task>
#   pgai_lock_acquired_epoch <content>
#   pgai_lock_held_seconds <content> [now_epoch]
#   pgai_lock_format_held_seconds <seconds>
#   pgai_lock_describe_held <content> [now_epoch]
#   pgai_lock_held_verdict <content> <threshold_seconds> [now_epoch]
#
# Exclusion semantics are UNCHANGED: every caller still takes a
# non-blocking exclusive flock (flock -n) and a contended caller still
# declines exactly as before -- only the record and the message improve.
#
# Provenance record shape (plain "key=value" lines, one field per line):
#   holder=<value>
#   pid=<value>
#   op=<value>
#   task=<value>
#   acquired_at=<ISO-8601 UTC, YYYY-MM-DDThh:mm:ssZ>
# Exactly these five fields and nothing else -- no environment dumps, no
# tokens, no command lines that could carry a credential. The first four
# are the caller's; acquired_at is stamped by pgai_lock_acquire itself, in
# the same staged write as the other four, so how long a lock has been
# held is a recorded fact rather than an inference from the file's mtime
# (which the held-lock refresh below keeps moving, and which therefore
# means "last touched", never "held since"). A caller with a legitimate
# reason to have no task to name yet (an OVERWATCH sweep, or a wake chain
# that has not dispatched its first task) passes the literal string "none"
# rather than an empty string; an empty string in any of the four caller
# fields is refused (see pgai_lock_acquire below). A chain names the task
# it dispatches later through pgai_lock_set_task.
#
# A record without a parseable acquired_at -- one written before the field
# existed, or one torn by a foreign writer -- is read as "acquired_at not
# recorded (pre-upgrade lock)" by every held-duration reader below; no
# reader ever substitutes the mtime or invents a duration for it.
#
# Write-ordering invariant (why the code below is shaped the way it is):
#   The record and the lock arrive together. pgai_lock_acquire never
#   writes into the file at the lock path. It stages the four fields in a
#   private sibling file (<lock_file>.tmp.<pid>), takes its flock on THAT
#   inode, and then moves the inode onto the lock path in one directory
#   operation: `ln` when nothing sits at the path (link(2) fails with
#   EEXIST rather than replacing, so it doubles as the atomic create), or
#   rename(2) over the previous file once this caller has won the flock on
#   it. A concurrent reader of the lock path therefore sees either no
#   file, the previous holder's complete record, or this holder's complete
#   record -- never an empty file and never a torn one -- and the inode it
#   reads is always the inode the holder's flock lives on. The previous
#   file at the path is only ever opened read-only, never truncated, so
#   losing the flock race can never disturb a live holder's record; and
#   whatever a crash leaves at the path is never truncated in place
#   either, only replaced by the next winner's staged inode. The same
#   reasoning lets pgai_lock_release unlink the path while STILL holding
#   the fd's lock (flock is advisory over lock ACQUISITION, not over
#   directory operations) and close that fd -- and so release the lock --
#   only after the path is already gone.
#
# Crash recovery:
#   The flock is what excludes; the provenance is a record, not a barrier.
#   A process that dies while holding the lock (crash, kill -9, OOM) has
#   every one of its file descriptors closed by the kernel on exit, which
#   releases its flock automatically -- the NEXT acquirer's flock -n always
#   succeeds regardless of what the lock file still says. Provenance left
#   behind by a pid that no longer exists is therefore never a barrier to
#   reacquiring the lock: pgai_lock_acquire replaces it unconditionally
#   once flock confirms the new caller holds the lock, and additionally
#   checks whether the PREVIOUS record's pid is still alive (`kill -0`)
#   purely to log an informational diagnostic ("previous holder pid <N> is
#   no longer running") for whoever is troubleshooting the acquisition
#   history -- that check never changes the return code or blocks the
#   acquisition. (The liveness check can be fooled by pid reuse or by a
#   pid owned by a different user, which reads as "not running" under
#   `kill -0`; this is a diagnostic best-effort, not a correctness
#   dependency -- correctness rests entirely on flock.)
#
# Truncate-free release:
#   A lock file without provenance must be impossible to leave behind --
#   the same law pgai_lock_acquire already enforces on the acquire side
#   (the refusal below) extended to the release side. pgai_lock_release
#   does exactly one thing on disk -- unlink the lock path -- and only
#   then closes the fd whose flock it held, so a clean release leaves no
#   on-disk trace at all. There is deliberately no prior truncate: an
#   emptied-but-still-linked file was an in-between state a contender
#   could observe while the releaser still held the flock, classify as
#   provenance-less, and reap by unlinking -- at which point the
#   releaser's own unlink on the next line removed whichever file the
#   reaper had just created, and a third caller acquired beside the
#   second. With the unlink as the only step, a waiter that opened the old
#   inode either loses the flock to the releaser and finds the path gone,
#   or wins the flock on an inode the path no longer names; both are
#   retried against the current path (see "Won on an orphan" below), and
#   a waiter that opens after the unlink creates a fresh lock. No
#   interleaving of the two sides can seat two holders.
#   What a crash can leave at the path is a complete record whose flock
#   died with its holder, which the next acquisition replaces. A
#   provenance-less file whose flock nobody holds -- a writer outside this
#   helper, or team/scripts/release-locks.sh swapping an empty file in for
#   a dead holder -- is replaced the same way and narrated as a recovery
#   sweep, so the replacement is never silent. A staging file a caller
#   died holding is litter that pgai_lock_acquire removes once its pid is
#   gone.
#
# Won on an orphan, and the other bounded retries:
#   Because the lock path can be unlinked (a release, a reap) or renamed
#   over (an acquisition) while a waiter has the previous inode open, a
#   flock -n that SUCCEEDS proves only that this caller holds that inode
#   -- not that the inode is still what the path names. pgai_lock_acquire
#   therefore checks, after every flock win on a pre-existing file, that
#   the path still names the inode it holds (_pgai_lock_path_verdict); a
#   win on an inode the path has abandoned is closed and the attempt
#   repeated against whatever sits at the path now. The same bounded
#   retry covers a path that vanished or changed inode between open and
#   flock, and a reap that finds the path no longer naming the file it
#   classified. The bound (_PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS) is a handful
#   of attempts per call; exhausting it means the path is being rewritten
#   faster than this caller can open it, and the call declines as
#   contended rather than spin.
#
# Contention-path staleness reaping:
#   A live, recently-acquired, well-provenanced holder is deferred exactly
#   as always -- flock contention is the correctness mechanism and nothing
#   here weakens it for the healthy path. But a waiter that loses the
#   contention race no longer defers UNCONDITIONALLY: pgai_lock_acquire
#   tests the existing holder's provenance before giving up, and reaps
#   (removes the stale lock file, narrates the evidence dual-channel, then
#   retries the acquisition once) only when the file carries no record
#   and is not fresh (condition below). A recorded pid that no longer
#   answers `kill -0` is NOT grounds to reap here: this path is reached
#   only when some live process still holds the flock, so a dead
#   recorded pid means the lock is held by a process the record does not
#   name -- an orphan that inherited the holder's lock descriptor (a
#   child that outlived a `kill -KILL` of its wake), a holder recorded
#   with a pid from another pid namespace, or a concurrent acquirer
#   between its flock win and its rename. A holder that really died has
#   already lost its flock in the kernel, and the flock-won branch above
#   replaces its record without any reap. The waiter therefore defers,
#   narrating lock.provenance.orphan_holder once per lock per process
#   with the pids that actually hold the lock file open (read from
#   /proc/<pid>/fd, best-effort).
#     No provenance, and not fresh -- the lock file carries no
#        holder/pid/op/task record (pgai_lock_describe_provenance reads
#        "(no provenance recorded)") AND its mtime is at least
#        _PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS old. A lock this
#        file's own acquire path takes carries its record from the instant
#        it appears at the path (see the write-ordering invariant), so a
#        provenance-less file is either a holder that predates or bypasses
#        this helper or the trace of a non-atomic writer: untrusted
#        evidence, not a barrier to reacquisition. The freshness condition
#        is what keeps a writer that is mid-flight -- a foreign holder that
#        opened and flocked but has not written yet, or a release swapping
#        files -- from being reaped in the milliseconds before its record
#        lands: a file younger than the window is deferred to like any
#        live holder, named in the contention narration as fresh and
#        unprovenanced, and re-examined on the next probe.
#   Before that reap unlinks, the reaper re-checks that the path still
#   names the inode it classified; a file replaced in the meantime is not
#   the file it judged, so that reap is abandoned and the attempt retried.
#   Age is evidence, never grounds to reap a live holder. The lock file's
#   mtime is refreshed on every successful acquisition and, while a task
#   runs, by the wake chain's own per-task watchdog poll loop
#   (pgai_lock_refresh_held_mtime below, called from team/scripts/lib/
#   wake_driver_invoke.sh -- see "Held-lock refresh" further down), so a
#   healthy holder's lock never ages past the chain-budget floor
#   pgai_lock_chain_budget_floor_seconds derives: max_runtime_seconds +
#   max_task_seconds + kill_grace_seconds + a fixed margin, the longest a
#   chain can legitimately hold the lock, since run_project_chain checks
#   its caps only BETWEEN tasks and the last task dispatched under the
#   cap may run its whole per-task budget past it. A lock whose age
#   exceeds the effective threshold (pgai_lock_stale_age_threshold_
#   seconds: that floor, or a larger configured wake-periods product --
#   pp_stale_lock_reap_after_wake_periods() in project_paths.sh, default
#   3, see kanban.cfg_example's [locks] section) while its recorded pid
#   still answers `kill -0` is a holder that is alive but no longer
#   refreshing: wedged (hung on I/O, a stuck external call, a frozen
#   cgroup), or running with the per-task timeout disabled. That holder is
#   NOT reaped. Unlinking a live holder's file does not stop the holder --
#   its flock lives on on the old inode -- it only hands the path to a
#   second chain, and two chains driving one project's dev tree and queue
#   files is exactly the split-brain team/scripts/release-locks.sh's
#   header describes. The waiter instead narrates
#   lock.provenance.holder_overdue once per lock per process -- naming the
#   age, the threshold and its derivation, and the holder's four fields --
#   and defers exactly as it does for any live holder. The remedy is the
#   operator's: release-locks.sh --release NAME --force signals that
#   holder so its own exit path releases the lock.
#   A reap is bounded to one per pgai_lock_acquire call (a per-call flag
#   guards it) so a pathological race that keeps recreating a stale-
#   looking lock cannot spin this function forever; a second consecutive
#   loss falls through to the ordinary contended refusal.
#
# Trap/finally guarantee (same bug, its same-day recurrence addendum):
#   No exit path of a process that acquired a lock through this file may
#   leave that lock held -- not normal completion, not an early return, not
#   a HALT-detected clean exit, not a provider refusal, not SIGTERM/SIGINT,
#   not an error exit under `set -e`. Every successful pgai_lock_acquire
#   call registers its lock_file/fd pair in _PGAI_LOCK_HELD_FDS (below);
#   pgai_lock_release clears its own entry when a caller calls it
#   explicitly, and pgai_lock_release_all_held -- installed once, when this
#   file is first sourced, as either the wake chain's existing
#   CLEANUP_ON_EXIT_PRE_HOOK (script_common.sh's cleanup_on_exit already
#   fires on EXIT/SIGTERM/SIGINT/SIGHUP for every wake and OVERWATCH process,
#   and stops the process group named by CLEANUP_ON_EXIT_PROCESS_GROUP --
#   the wake's running provider -- BEFORE it calls this hook, so no lock is
#   released while a provider that relies on it is still running) or,
#   absent any such trap, this file's own EXIT/SIGTERM/SIGINT trap --
#   releases whatever is left in the registry on the way out. The fix lives
#   in exactly this one file rather than at each of the wake chain's exit
#   sites; see the installation block at the bottom of this file for why
#   the two integration modes are both needed and how they compose safely
#   with a caller's own trap instead of displacing it.
#
# Chain-level ownership reverification:
#   The staleness reaper above closes the window where a WAITER wrongly
#   defers to a holder that left no record. It does nothing for the
#   opposite failure: a chain that legitimately acquired this lock, lost
#   its file while still alive (a by-hand deletion of the lock file, or a
#   reaper from an older install), and then kept right on running -- driving
#   its own dev-tree worktrees and writing its own queue files as though
#   nothing had happened, alongside whichever new chain the reap just let
#   in. Two chains racing the same dev tree and the same queue files is
#   the harm this section closes.
#
#   pgai_lock_still_held / pgai_lock_reverify_or_narrate below give a chain
#   a cheap, read-only way to ask "do I still actually hold this?" at every
#   mutating phase boundary of its own run (agent dispatch, the post-agent
#   merge check, and the queue-marker write -- see
#   team/scripts/lib/wake_driver.sh's process_one_task and
#   team/scripts/lib/wake_driver_finalize.sh's wd_finalize_task, the three
#   call sites). No flock acquisition and no sleep: a chain-level lock is
#   held for a whole wake chain's batch, so a per-boundary check that
#   itself contended for the lock or paused would be far too expensive to
#   run unconditionally on every boundary of every chain. The check is a
#   plain read of the lock file's current provenance and a string compare
#   against this process's own pid -- if a NEW holder's provenance is
#   there instead, this chain lost the lock at some point since its last
#   check, and pgai_lock_reverify_or_narrate says so, naming the new
#   holder's four fields, so the caller can stop at that boundary instead
#   of performing it.
#
#   The mirror-image release problem this creates: once a chain knows it
#   no longer holds the lock, it must never call pgai_lock_release on the
#   path anyway -- that call unlinks whatever currently sits at the
#   path, which by now is the NEW holder's own record. pgai_lock_abandon is the release-shaped counterpart for
#   exactly this case -- it closes this process's own fd and drops its
#   _PGAI_LOCK_HELD_FDS bookkeeping entry (so the trap/finally sweep above
#   never later calls pgai_lock_release on this path either) without
#   touching the on-disk file at all.
#
# Hold discipline and ownership-checked release:
#   The per-project repo lock a wake chain takes (run_project_chain,
#   team/scripts/lib/wake_driver.sh) is held on ONE open fd for the whole
#   chain -- through every dispatched subagent invocation, the post-agent
#   merge check, and wd_finalize_task's queue-marker write -- and released
#   once, at the chain's end. It is never released and re-taken around a
#   subagent: the provider CLI and everything it spawns inherit the fd, and
#   an inherited fd holds the flock (only closing EVERY fd on the open file
#   description, or an explicit unlock, drops it). A sibling chain that
#   wakes while a live, in-budget holder is running therefore loses the
#   flock -n race and defers; the one reap condition above (no
#   provenance, not fresh) is the only way a waiter ever takes a held
#   lock -- neither age nor a dead recorded pid ever is.
#
#   The release side is where a held lock could still be lost. Both
#   pgai_lock_release's unlink and the trap/finally sweep act on the
#   PATH, and the path stops being this process's file the moment another
#   process reaps the lock and reacquires it (the reaper unlinks the old
#   file; the new acquisition creates a new one). A release that
#   unlinked whatever sat at the path would then free the NEW holder's
#   lock mid-run -- the next waiter's flock -n would succeed on a fresh
#   inode while that holder's own subagent was still working, and that
#   holder would lose the lock in turn at its next boundary, so one lost
#   lock would hand itself down chain after chain. pgai_lock_release is
#   therefore ownership-checked: it unlinks the path only when the file
#   there is the very inode its fd holds the flock on (falling
#   back to comparing the record's pid with the pid recorded at
#   acquisition where inode evidence is unavailable); otherwise it degrades
#   to pgai_lock_abandon -- close own fd, drop bookkeeping, touch nothing
#   -- and narrates dual-channel (lock.provenance.release_not_owner). The
#   sweep inherits that check, so no exit path of a process that lost a
#   lock can disturb the process that now holds it; a lock this process no
#   longer holds per its own bookkeeping is likewise left alone.
#
# Held-lock refresh:
#   Age can only tell a wedged holder from a working one if a working
#   holder's lock keeps looking fresh. pgai_lock_acquire's provenance write
#   sets the mtime once; a chain then holds the lock for hours through
#   subagent invocations that write nothing to it. pgai_lock_refresh_held_
#   mtime is the cheap, flock-free refresh for that gap: a plain `touch`
#   of the path, performed only when the file at the path is the very
#   inode the caller's fd holds the flock on (the same ownership check
#   pgai_lock_release makes), so a chain that has since lost the lock
#   never freshens the new holder's file. It never takes, re-takes, or
#   releases the flock -- flock is advisory over acquisition, not over
#   ordinary I/O, exactly as the write-ordering invariant above relies on.
#   The wake chain's per-task watchdog poll loop calls it every
#   _PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS while the provider runs
#   (team/scripts/lib/wake_driver_invoke.sh). With the per-task timeout
#   disabled (max_task_seconds = 0) there is no poll loop and so no
#   refresh; that costs diagnosability, not safety -- a live holder is
#   never reaped on age, so the only effect is an overdue narration
#   against a long-running task.

_PGAI_LOCK_PROVENANCE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=error_contract.sh
source "${_PGAI_LOCK_PROVENANCE_LIB_DIR}/error_contract.sh"
# shellcheck source=ini_parser.sh
# read_ini backs _pgai_lock_wake_cfg_seconds's kanban.cfg [wake] lookups
# (pgai_lock_chain_budget_floor_seconds) -- guarded exactly like
# project_paths.sh's own multiple-sourcing convention so a caller that
# already sourced ini_parser.sh (project_paths.sh, most wake call sites)
# never redefines it a second time.
if ! declare -F read_ini >/dev/null 2>&1; then
    source "${_PGAI_LOCK_PROVENANCE_LIB_DIR}/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# _PGAI_LOCK_HELD_FDS -- lock_file -> open fd, for every lock THIS process
# currently holds. pgai_lock_acquire adds an entry on success; whichever of
# an explicit pgai_lock_release call or the pgai_lock_release_all_held sweep
# runs first removes it. Safe to re-declare if this file is sourced more
# than once in the same process (an existing associative array's contents
# survive a repeat `declare -A`, matching project_paths.sh's own multiple-
# sourcing convention for its own arrays).
# ---------------------------------------------------------------------------
declare -gA _PGAI_LOCK_HELD_FDS 2>/dev/null || true

# ---------------------------------------------------------------------------
# _PGAI_LOCK_HELD_PIDS -- lock_file -> the pid this process wrote into the
# provenance record when it acquired that lock. Maintained in lockstep with
# _PGAI_LOCK_HELD_FDS (added on acquisition, dropped by release and
# abandon); pgai_lock_release's ownership check reads it as the fallback
# comparison against the on-disk record when inode evidence is unavailable,
# and names it in the release_not_owner narration. Same re-declaration
# safety as the fd table above.
# ---------------------------------------------------------------------------
declare -gA _PGAI_LOCK_HELD_PIDS 2>/dev/null || true

# ---------------------------------------------------------------------------
# _pgai_lock_field <content> <key>
# Echo the value of "<key>=" from a provenance record's text, or nothing if
# absent. Internal helper -- not part of this file's public surface.
# ---------------------------------------------------------------------------
_pgai_lock_field() {
    local content="$1" key="$2"
    printf '%s\n' "$content" | sed -n "s/^${key}=//p" | head -n1
}

# ---------------------------------------------------------------------------
# pgai_lock_field <content> <key>
# Echo the value of the named field ("holder", "pid", "op", or "task") from
# a provenance record's text, or nothing if that field is absent. Public
# accessor for a caller that needs ONE field in isolation -- e.g. a
# staleness check that must test a specific pid's liveness -- rather than
# the full one-line description pgai_lock_describe_provenance renders. Thin
# wrapper around the same internal field extraction _pgai_lock_field
# already does, so a caller with this narrower need still shares the one
# parser instead of writing a second one.
# ---------------------------------------------------------------------------
pgai_lock_field() {
    _pgai_lock_field "$1" "$2"
}

# ---------------------------------------------------------------------------
# pgai_lock_describe_provenance <content>
# Echo a one-line "holder=... pid=... op=... task=..." description of a
# provenance record's text, or "(no provenance recorded)" when the content
# has no recognizable fields -- e.g. a lock file from before this helper
# existed, or one held by a caller that does not use it. Public formatter --
# pairs with pgai_lock_read_provenance so a contention-message caller (a
# declined project-lock acquisition, a diagnostic sweep) can turn the raw
# record it reads into the same one-line wording pgai_lock_acquire's own
# contention message uses below, without re-deriving the field layout.
# ---------------------------------------------------------------------------
pgai_lock_describe_provenance() {
    local content="$1"
    local holder pid op task
    holder="$(_pgai_lock_field "$content" holder)"
    pid="$(_pgai_lock_field "$content" pid)"
    op="$(_pgai_lock_field "$content" op)"
    task="$(_pgai_lock_field "$content" task)"
    if [[ -z "$holder" && -z "$pid" && -z "$op" && -z "$task" ]]; then
        echo "(no provenance recorded)"
        return 0
    fi
    echo "holder=${holder:-<unknown>} pid=${pid:-<unknown>} op=${op:-<unknown>} task=${task:-<unknown>}"
}

# ---------------------------------------------------------------------------
# pgai_proc_start_time <pid>
# Echo <pid>'s start time in clock ticks since boot (field 22 of
# /proc/<pid>/stat), or nothing when the pid is not a number or its stat
# file cannot be read. The command name in field 2 may hold spaces and
# parentheses, so the fields are counted from after its LAST ')'. Paired
# with the pid in a record, it tells the process that wrote the record
# apart from a later process that reused the same pid. Always exits 0.
# ---------------------------------------------------------------------------
pgai_proc_start_time() {
    local pid="${1:-}" stat rest
    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    { stat="$(< "/proc/${pid}/stat")"; } 2>/dev/null || return 0
    rest="${stat##*) }"
    local -a fields
    read -r -a fields <<< "$rest"
    # fields[0] is field 3 (state), so field 22 (starttime) is fields[19].
    [[ "${fields[19]:-}" =~ ^[0-9]+$ ]] && printf '%s\n' "${fields[19]}"
    return 0
}

# ---------------------------------------------------------------------------
# pgai_boot_id
# Echo this boot's id (/proc/sys/kernel/random/boot_id), or nothing where
# the kernel does not expose one. A pid recorded under another boot names a
# process that no longer exists, whatever now answers on that number.
# Always exits 0.
# ---------------------------------------------------------------------------
pgai_boot_id() {
    local id=""
    { id="$(< /proc/sys/kernel/random/boot_id)"; } 2>/dev/null || true
    [[ -n "$id" ]] && printf '%s\n' "$id"
    return 0
}

# ---------------------------------------------------------------------------
# pgai_lock_pid_liveness_verdict <content>
# Echo "held" | "stale" | "unparseable" for a provenance record's raw text:
#   held        -- the recorded pid answers `kill -0` (still running, or at
#                  least a live pid this user can signal-probe), and it is
#                  the same process the record names (see below).
#   stale       -- the recorded pid does not answer `kill -0`, or it answers
#                  but is a different process: the record's boot_id differs
#                  from this boot's, or its pid_start differs from that
#                  pid's start time now (the pid was reused after the
#                  recorded process ended -- after a container restart pids
#                  are small and come back quickly).
#   unparseable -- the record carries no recognizable pid at all (no
#                  provenance whatsoever, or a record whose pid field is
#                  blank).
# This is evidence, not inference -- a caller that wants to act on the
# verdict should still say WHY (the pid it checked, or the fact that none
# was recorded) rather than the bare word alone. Shared by
# pgai_lock_acquire's contention-path reaper (below) and
# team/scripts/release-locks.sh's operator-facing lock listing, which
# used to run this same kill -0 check inline -- one liveness classifier
# for the whole framework, not two copies that could drift apart.
# ---------------------------------------------------------------------------
pgai_lock_pid_liveness_verdict() {
    local content="$1"
    local desc pid
    desc="$(pgai_lock_describe_provenance "$content")"
    if [[ "$desc" == "(no provenance recorded)" ]]; then
        echo "unparseable"
        return 0
    fi
    pid="$(_pgai_lock_field "$content" pid)"
    if [[ -z "$pid" ]]; then
        echo "unparseable"
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "stale"
        return 0
    fi
    # Identity checks apply only to a record that carries the field and a
    # host that can answer it; a record without them (every lock file, a
    # record from before they were written) reads exactly as before.
    local recorded current
    recorded="$(_pgai_lock_field "$content" boot_id)"
    if [[ -n "$recorded" ]]; then
        current="$(pgai_boot_id)"
        if [[ -n "$current" && "$current" != "$recorded" ]]; then
            echo "stale"
            return 0
        fi
    fi
    recorded="$(_pgai_lock_field "$content" pid_start)"
    if [[ -n "$recorded" ]]; then
        current="$(pgai_proc_start_time "$pid")"
        if [[ -n "$current" && "$current" != "$recorded" ]]; then
            echo "stale"
            return 0
        fi
    fi
    echo "held"
}

# ---------------------------------------------------------------------------
# pgai_lock_format_age_seconds <seconds>
# Echo a short human-readable age string ("Xs", "Xm Ys", or "Xh Ym") for a
# duration in seconds. Shared formatter for anything that reports a lock's
# age to an operator or in a narration line -- pgai_lock_acquire's
# staleness-reap narration (below) and team/scripts/lib/why_details.sh's
# lock-listing pane both call this instead of each carrying its own
# ad-hoc formatting.
# ---------------------------------------------------------------------------
pgai_lock_format_age_seconds() {
    local secs="${1:-0}"
    [[ "$secs" =~ ^-?[0-9]+$ ]] || secs=0
    (( secs < 0 )) && secs=0
    if (( secs < 60 )); then
        printf '%ss' "${secs}"
    elif (( secs < 3600 )); then
        printf '%sm %ss' "$(( secs / 60 ))" "$(( secs % 60 ))"
    else
        printf '%sh %sm' "$(( secs / 3600 ))" "$(( (secs % 3600) / 60 ))"
    fi
}

# ---------------------------------------------------------------------------
# _PGAI_LOCK_NO_ACQUIRED_AT
# The one wording every held-duration reader uses for a record that carries
# no parseable acquired_at (see "Provenance record shape" in this file's
# header). Shared so the operator sees the same phrase on every surface.
# ---------------------------------------------------------------------------
_PGAI_LOCK_NO_ACQUIRED_AT="acquired_at not recorded (pre-upgrade lock)"

# ---------------------------------------------------------------------------
# pgai_lock_acquired_epoch <content>
# Echo the epoch seconds of a provenance record's acquired_at field and
# return 0; echo nothing and return 1 when the field is absent or torn.
# Liberal in what it reads: a "T" or a space between date and time, an
# optional fractional second, and a "Z" or a +hh:mm / -hh:mm offset are all
# accepted -- the writer produces only the canonical "...Z" form. Anything
# date(1) then refuses to parse also returns 1, so a torn value is never
# turned into a duration.
# ---------------------------------------------------------------------------
pgai_lock_acquired_epoch() {
    local content="$1" value epoch
    value="$(_pgai_lock_field "$content" acquired_at)"
    value="${value//[[:space:]]/ }"
    value="${value## }"; value="${value%% }"
    [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[T\ ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})$ ]] || return 1
    epoch="$(date -u -d "$value" +%s 2>/dev/null)" || return 1
    [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
    echo "$epoch"
}

# ---------------------------------------------------------------------------
# pgai_lock_held_seconds <content> [now_epoch]
# Echo how many whole seconds the lock described by <content> has been held
# -- now minus acquired_at, never the file's mtime -- and return 0; return 1
# with no output when acquired_at is not recorded. A clock that reads
# earlier than acquired_at clamps to 0. <now_epoch> defaults to the current
# time; a caller rendering several locks passes one value for all of them.
# ---------------------------------------------------------------------------
pgai_lock_held_seconds() {
    local content="$1" now="${2:-}" acquired held
    acquired="$(pgai_lock_acquired_epoch "$content")" || return 1
    [[ "$now" =~ ^[0-9]+$ ]] || now="$(date +%s)"
    held=$(( now - acquired ))
    (( held < 0 )) && held=0
    echo "$held"
}

# ---------------------------------------------------------------------------
# pgai_lock_format_held_seconds <seconds>
# Echo a held duration at the granularity an operator reads a lock at:
# "Ns" under a minute, "Nm" under an hour, "Nh Mm" beyond. Coarser than
# pgai_lock_format_age_seconds on purpose -- a lock held 5 minutes reads
# "5m", not "5m 3s".
# ---------------------------------------------------------------------------
pgai_lock_format_held_seconds() {
    local secs="${1:-0}"
    [[ "$secs" =~ ^[0-9]+$ ]] || secs=0
    if (( secs < 60 )); then
        printf '%ss' "$secs"
    elif (( secs < 3600 )); then
        printf '%sm' "$(( secs / 60 ))"
    else
        printf '%sh %sm' "$(( secs / 3600 ))" "$(( (secs % 3600) / 60 ))"
    fi
}

# ---------------------------------------------------------------------------
# pgai_lock_describe_held <content> [now_epoch]
# Echo the one-line held-duration description every lock-listing surface
# shows (why.sh --details, release-locks.sh):
#   held: <duration> (since <acquired_at>) by <holder> pid <pid> -- <task>
# where <task> reads "chain, no task yet" while the record's task is
# "none" (a chain that has not dispatched a task). A record without a
# parseable acquired_at renders "held: acquired_at not recorded
# (pre-upgrade lock) by ..." instead -- no duration is invented for it.
# The separator before <task> is an em dash.
# ---------------------------------------------------------------------------
pgai_lock_describe_held() {
    local content="$1" now="${2:-}"
    local holder pid task acquired_at held_secs who
    holder="$(_pgai_lock_field "$content" holder)"
    pid="$(_pgai_lock_field "$content" pid)"
    task="$(_pgai_lock_field "$content" task)"
    acquired_at="$(_pgai_lock_field "$content" acquired_at)"
    if [[ -z "$task" || "$task" == "none" ]]; then
        task="chain, no task yet"
    fi
    who="by ${holder:-<unknown>} pid ${pid:-<unknown>} — ${task}"
    if held_secs="$(pgai_lock_held_seconds "$content" "$now")"; then
        printf 'held: %s (since %s) %s\n' "$(pgai_lock_format_held_seconds "$held_secs")" "$acquired_at" "$who"
    else
        printf 'held: %s %s\n' "$_PGAI_LOCK_NO_ACQUIRED_AT" "$who"
    fi
}

# ---------------------------------------------------------------------------
# pgai_lock_held_verdict <content> <threshold_seconds> [now_epoch]
# Echo the one-word verdict that pairs with pgai_lock_describe_held,
# comparing how long the lock has been held against <threshold_seconds>
# (callers pass pgai_lock_stale_age_threshold_seconds -- the stale floor):
#   live                    held no longer than the floor
#   long (Nm past floor)    held past the floor; N is the overage in whole
#                           minutes, rounded up
#   dead (pid N not running) the recorded pid does not answer kill -0, so
#                           the duration is no longer a live holder's
# Echoes nothing (return 1) when acquired_at is not recorded -- the
# description line already says so, and there is no duration to judge.
# ---------------------------------------------------------------------------
pgai_lock_held_verdict() {
    local content="$1" threshold="${2:-}" now="${3:-}" held_secs over
    held_secs="$(pgai_lock_held_seconds "$content" "$now")" || return 1
    if [[ "$(pgai_lock_pid_liveness_verdict "$content")" == "stale" ]]; then
        echo "dead (pid $(_pgai_lock_field "$content" pid) not running)"
        return 0
    fi
    [[ "$threshold" =~ ^[0-9]+$ ]] || threshold="$(pgai_lock_stale_age_threshold_seconds)"
    if (( held_secs > threshold )); then
        over=$(( (held_secs - threshold + 59) / 60 ))
        echo "long (${over}m past floor)"
    else
        echo "live"
    fi
}

# ---------------------------------------------------------------------------
# _pgai_lock_held_phrase <content>
# Echo a short in-sentence held phrase for narrations ("held 5m since
# 2026-01-01T00:00:00Z", or the not-recorded wording). Internal helper for
# pgai_lock_acquire's reap and overdue narrations.
# ---------------------------------------------------------------------------
_pgai_lock_held_phrase() {
    local content="$1" held_secs
    if held_secs="$(pgai_lock_held_seconds "$content")"; then
        printf 'held %s since %s' "$(pgai_lock_format_held_seconds "$held_secs")" "$(_pgai_lock_field "$content" acquired_at)"
    else
        printf '%s' "$_PGAI_LOCK_NO_ACQUIRED_AT"
    fi
}

# ---------------------------------------------------------------------------
# _PGAI_LOCK_WAKE_PERIOD_SECONDS
# Length of one wake period in seconds -- this framework's standard
# per-agent cron cadence (each agent fires every two minutes; see the
# recommended crontab schedule in README.md / templates/install/
# crontab.example), used to turn the configurable wake-period COUNT
# (pp_stale_lock_reap_after_wake_periods() in project_paths.sh) into a
# wall-clock age threshold below. Only the count is a tunable; the period
# length itself is not independently configurable.
# ---------------------------------------------------------------------------
_PGAI_LOCK_WAKE_PERIOD_SECONDS=120

# ---------------------------------------------------------------------------
# _PGAI_LOCK_CHAIN_BUDGET_MARGIN_SECONDS
# Buffer added on top of max_runtime_seconds + max_task_seconds +
# kill_grace_seconds when deriving the chain-budget floor below
# (_pgai_lock_resolve_chain_budget) -- one wake period's
# worth of slack for cron dispatch jitter and the lock file's own mtime
# granularity, so the floor never lands exactly on a legitimate chain's
# last live second. Not independently configurable, same posture as
# _PGAI_LOCK_WAKE_PERIOD_SECONDS above.
# ---------------------------------------------------------------------------
_PGAI_LOCK_CHAIN_BUDGET_MARGIN_SECONDS=120

# ---------------------------------------------------------------------------
# _PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS
# How often, in seconds, a chain's per-task watchdog poll loop refreshes
# its held lock's mtime through pgai_lock_refresh_held_mtime (see
# "Held-lock refresh" in this file's header). Coarse on purpose: the age
# threshold it keeps a live holder under is measured in hours, and each
# refresh costs a stat pair and a touch. Not independently configurable,
# same posture as the two constants above.
# ---------------------------------------------------------------------------
_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS=60

# ---------------------------------------------------------------------------
# _PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS
# Minimum age, in whole seconds since last modification, a contended lock
# file with NO provenance record must have before pgai_lock_acquire's
# contention classifier reaps it (see "Contention-path staleness reaping"
# in this file's header, condition 2). Below this a provenance-less file
# is deferred to as ordinary contention: a writer outside this helper may
# have created and flocked it and not written yet, and a release may be
# mid-swap. Five seconds is orders of magnitude longer than either
# transition takes and orders of magnitude shorter than one wake period,
# so a genuinely abandoned empty file still costs at most one deferred
# probe before it is reaped. A constant, not a kanban.cfg key: there is
# no deployment in which a different value would be more correct, so
# exposing it would only add a knob that can be set wrong -- the same
# posture as the three constants above.
# ---------------------------------------------------------------------------
_PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS=5

# ---------------------------------------------------------------------------
# _PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS
# How many times one pgai_lock_acquire call will re-open the lock path
# after finding that the inode it opened is no longer the inode the path
# names -- a release or a reap unlinked it, or another acquisition renamed
# over it, between this caller's open and its flock (see "Won on an
# orphan" in this file's header). Each such retry is evidence that another
# process just finished with the path, so the next attempt normally
# succeeds or contends cleanly; the bound exists so that a path being
# rewritten continuously can never spin a caller, and a call that exhausts
# it declines as contended. Not configurable, same posture as the
# constants above.
# ---------------------------------------------------------------------------
_PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS=4

# ---------------------------------------------------------------------------
# _PGAI_LOCK_WAIT_FIRST_BACKOFF_SECONDS / _PGAI_LOCK_WAIT_MAX_BACKOFF_SECONDS
# The sleep between two contended attempts of pgai_lock_acquire_waiting
# (below): the first pause is _FIRST, each later one doubles, and no single
# pause exceeds _MAX or the time left before the caller's wait budget runs
# out. The caller chooses how long to wait in total; these only shape the
# probing inside that budget, so a holder that releases a moment after the
# first probe is picked up within a second while a long hold is probed a
# handful of times rather than continuously. Not configurable, same
# posture as the constants above.
# ---------------------------------------------------------------------------
_PGAI_LOCK_WAIT_FIRST_BACKOFF_SECONDS=1
_PGAI_LOCK_WAIT_MAX_BACKOFF_SECONDS=8

# ---------------------------------------------------------------------------
# _pgai_lock_wake_cfg_seconds <env_name> <ini_key> <default>
# Resolve one [wake] kanban.cfg tunable (max_runtime_seconds,
# max_task_seconds, kill_grace_seconds) as a whole positive-integer number
# of seconds, for use by _pgai_lock_resolve_chain_budget below. Precedence mirrors
# config_loader.sh's own load_kanban_cfg_to_env for the same keys: an
# already-exported env var of the same name wins (the "Override at
# invocation: MAX_RUNTIME_SECONDS=<N> wake-batch.sh ..." form
# kanban.cfg_example documents), else kanban.cfg's [wake] section, else
# <default> -- so the chain-budget floor derived from these three values
# always matches what the wake loop itself is actually bound by, never a
# second, independently-drifting copy of the same number.
#
# Degrades to <default> (never fails loud) when KANBAN_ROOT is unset,
# kanban.cfg is unreadable, or read_ini is not loaded -- the case for a
# caller that sources this file standalone (a unit test, a script with no
# kanban-root context), which never had a config lookup to make in the
# first place, mirroring pgai_lock_stale_age_threshold_seconds's own
# degrade-to-default posture below.
# ---------------------------------------------------------------------------
_pgai_lock_wake_cfg_seconds() {
    local env_name="$1" ini_key="$2" default="$3"
    local env_val="${!env_name:-}"
    if [[ "$env_val" =~ ^[0-9]+$ ]]; then
        echo "$env_val"
        return 0
    fi
    if [[ -n "${KANBAN_ROOT:-}" && -f "${KANBAN_ROOT}/kanban.cfg" ]] \
        && declare -F read_ini >/dev/null 2>&1; then
        local cfg_val
        cfg_val="$(read_ini "${KANBAN_ROOT}/kanban.cfg" wake "$ini_key" "$default" 2>/dev/null)"
        if [[ "$cfg_val" =~ ^[0-9]+$ ]]; then
            echo "$cfg_val"
            return 0
        fi
    fi
    echo "$default"
}

# ---------------------------------------------------------------------------
# _PGAI_LOCK_BUDGET_MAX_RUNTIME / _MAX_TASK / _KILL_GRACE / _FLOOR
# Sidecar set: the chain budget's three configured addends and their sum,
# written together by _pgai_lock_resolve_chain_budget below. Call that
# resolver DIRECTLY, never through `$(...)` -- a subshell's assignments
# never reach the calling shell (the threshold sidecar pair further down
# documents the same rule). One derivation, read by
# pgai_lock_chain_budget_floor_seconds and by _pgai_lock_resolve_stale_
# threshold's derivation strings, so the floor and the words that explain
# it can never disagree.
# ---------------------------------------------------------------------------
_PGAI_LOCK_BUDGET_MAX_RUNTIME=""
_PGAI_LOCK_BUDGET_MAX_TASK=""
_PGAI_LOCK_BUDGET_KILL_GRACE=""
_PGAI_LOCK_BUDGET_FLOOR=""

# ---------------------------------------------------------------------------
# _pgai_lock_resolve_chain_budget
# Resolve, into the sidecar set above, the maximum wall-clock seconds a
# single legitimate wake chain can hold this lock for:
#     max_runtime_seconds  the wake loop's own walltime cap -- checked only
#                          BETWEEN tasks (run_project_chain, wake_driver.sh)
#   + max_task_seconds     so the last task dispatched just under that cap
#                          may run its whole per-task budget past it
#   + kill_grace_seconds   the SIGTERM-to-SIGKILL window that task, and then
#                          the chain itself, may still be legitimately
#                          alive and cleaning up in
#   + _PGAI_LOCK_CHAIN_BUDGET_MARGIN_SECONDS
#                          dispatch-jitter/mtime-granularity buffer
# This is the floor pgai_lock_stale_age_threshold_seconds below never lets
# the age threshold fall under: a chain that legitimately holds this lock
# for its whole batch, last task included, is not "wedged" merely because
# it outlasted a handful of wake periods. max_task_seconds = 0 (per-task
# timeout disabled) contributes nothing -- the chain is then bounded only
# between tasks and a task's length is unknowable, one more reason a live
# holder is never reaped on age (see "Contention-path staleness reaping"
# in this file's header).
# ---------------------------------------------------------------------------
_pgai_lock_resolve_chain_budget() {
    _PGAI_LOCK_BUDGET_MAX_RUNTIME="$(_pgai_lock_wake_cfg_seconds MAX_RUNTIME_SECONDS max_runtime_seconds 14400)"
    _PGAI_LOCK_BUDGET_MAX_TASK="$(_pgai_lock_wake_cfg_seconds MAX_TASK_SECONDS max_task_seconds 5400)"
    _PGAI_LOCK_BUDGET_KILL_GRACE="$(_pgai_lock_wake_cfg_seconds KILL_GRACE_SECONDS kill_grace_seconds 30)"
    _PGAI_LOCK_BUDGET_FLOOR=$(( _PGAI_LOCK_BUDGET_MAX_RUNTIME + _PGAI_LOCK_BUDGET_MAX_TASK \
        + _PGAI_LOCK_BUDGET_KILL_GRACE + _PGAI_LOCK_CHAIN_BUDGET_MARGIN_SECONDS ))
}

# ---------------------------------------------------------------------------
# _pgai_lock_chain_budget_addends
# Echo the one-line human-readable account of the floor's addends, from
# the sidecar set _pgai_lock_resolve_chain_budget last wrote -- e.g.
# "max_runtime_seconds 14400s + max_task_seconds 5400s + kill_grace_seconds
# 30s + 120s margin". Every narration and derivation string that explains
# the floor renders it through this one formatter.
# ---------------------------------------------------------------------------
_pgai_lock_chain_budget_addends() {
    printf 'max_runtime_seconds %ss + max_task_seconds %ss + kill_grace_seconds %ss + %ss margin' \
        "$_PGAI_LOCK_BUDGET_MAX_RUNTIME" "$_PGAI_LOCK_BUDGET_MAX_TASK" \
        "$_PGAI_LOCK_BUDGET_KILL_GRACE" "$_PGAI_LOCK_CHAIN_BUDGET_MARGIN_SECONDS"
}

# ---------------------------------------------------------------------------
# pgai_lock_chain_budget_floor_seconds
# `$(...)`-safe public wrapper: echo the chain-budget floor in seconds --
# see _pgai_lock_resolve_chain_budget above for the derivation.
# ---------------------------------------------------------------------------
pgai_lock_chain_budget_floor_seconds() {
    _pgai_lock_resolve_chain_budget
    echo "$_PGAI_LOCK_BUDGET_FLOOR"
}

# ---------------------------------------------------------------------------
# _PGAI_LOCK_LAST_THRESHOLD_SECONDS / _PGAI_LOCK_LAST_THRESHOLD_DERIVATION
# Sidecar pair (same "last-*" convention as e.g. DEFER_POSTURE_LAST_ERROR_
# FILE in defer_posture.sh) set together by _pgai_lock_resolve_stale_
# threshold below: the effective age-alone stale-lock threshold it just
# computed, and a one-line human-readable account of which inputs produced
# it -- the configured wake-periods product, the chain-budget floor and
# its own addends, and which of the two won.
#
# _pgai_lock_resolve_stale_threshold must be called DIRECTLY (never
# through `$(...)`) for these to land: command substitution runs the
# function in a subshell, and a subshell's variable assignments never
# reach the calling shell back -- only what it writes to stdout survives
# that boundary. Every caller in this file that needs both the number and
# the derivation together (pgai_lock_acquire's overdue-holder narration
# below) calls the resolver directly and reads both globals afterward,
# in the SAME shell, exactly once, so the resulting narration never
# double-fires the clamp warning below. pgai_lock_stale_age_threshold_
# seconds and pgai_lock_stale_age_threshold_derivation are the two
# `$(...)`-safe public wrappers for a caller that only wants one piece.
# ---------------------------------------------------------------------------
_PGAI_LOCK_LAST_THRESHOLD_SECONDS=""
_PGAI_LOCK_LAST_THRESHOLD_DERIVATION=""

# ---------------------------------------------------------------------------
# Narration-rate memoization: a long chain probes a
# contended lock repeatedly -- once per wake firing from every waiting
# agent, and (within a single firing) once on the first pass plus once
# more on wake_common.sh's end-of-iteration retry -- and the two dual-
# channel narrations below (lock.provenance.contended,
# lock.stale_threshold.floor_enforced) used to fire on every one of those
# probes, flooding the schedule window's event band with repeats of the
# same two lines. These process-scoped tables cap each narration to at
# most once per lock per process while still counting every probe, so a
# caller giving up can report "N probes" in its own closing summary
# without the dashboard ever seeing more than one occurrence per process.
# Reset per process by construction (a fresh bash invocation gets fresh,
# empty arrays) -- never persisted across wake firings, which is
# correct: each firing is its own process and narrates its own first
# contention once, exactly as this bug's acceptance describes.
# ---------------------------------------------------------------------------
declare -gA _PGAI_LOCK_CONTENDED_NARRATED=()
declare -gA _PGAI_LOCK_CONTENDED_PROBE_COUNT=()
# Same once-per-lock-per-process cap for the overdue-holder narration
# (lock.provenance.holder_overdue, pgai_lock_acquire below): a waiter that
# keeps probing a wedged holder says so once, then only the contended
# probe count keeps rising.
declare -gA _PGAI_LOCK_OVERDUE_NARRATED=()
# Same cap for the orphan-holder narration (lock.provenance.orphan_holder,
# pgai_lock_acquire below): the flock is held while the recorded pid is
# dead, and a waiter that keeps probing says so once.
declare -gA _PGAI_LOCK_ORPHAN_NARRATED=()
# Last floor-enforced derivation string this process has already
# narrated (empty = never narrated yet). Compared, not just checked for
# non-empty, so a clamp whose derivation CHANGES mid-process (a config
# reload between calls) still narrates the new value once -- "narrate
# once per process, or only when the clamped value changes" per this
# bug's fix description.
_PGAI_LOCK_FLOOR_NARRATED_DERIVATION=""

# ---------------------------------------------------------------------------
# _pgai_lock_resolve_stale_threshold [project] [narrate]
# Compute the wall-clock age (seconds) beyond which a contended lock's
# live holder counts as overdue -- no longer refreshing its lock, so
# wedged or unbounded; see "Contention-path staleness reaping" in this
# file's header for why that is narrated, never reaped -- and set _PGAI_LOCK_LAST_THRESHOLD_
# SECONDS / _PGAI_LOCK_LAST_THRESHOLD_DERIVATION (above) to the result and
# its derivation. Call DIRECTLY, never via `$(...)` -- see the sidecar
# pair's own header comment for why.
#
# The resolved value is never just the configured wake-periods product
# (pp_stale_lock_reap_after_wake_periods(), project_paths.sh): it is
# max(configured periods x period length, pgai_lock_chain_budget_floor_
# seconds). A wake chain legitimately holds this lock for its whole batch,
# so an age threshold armed only with a small wake-periods count would
# call a live, on-budget chain wedged; the floor makes that impossible
# regardless of how the count is configured. A configured product below
# the floor is CLAMPED to the floor -- rather than honored verbatim (which
# would let the threshold call a live, on-budget chain wedged)
# or refused outright (which would fail an ordinary acquisition over a
# config value that is merely too aggressive) -- and the caller always
# receives the larger, safer effective value regardless of <narrate>.
#
# <narrate>, the second argument, gates ONLY whether a clamp additionally
# fires the dual-channel narration below (see the CENSUS-ANCHOR) -- pass
# the literal string "narrate" from a call site that is itself ACTING on
# the clamped value (pgai_lock_acquire's contention classification, the
# one caller that passes it); omit it (the default) from a read-only display path
# that must never have a side effect merely from being asked what the
# threshold is. pgai_emit_error_contract's own refusal-ledger recording
# (team/SOP-core.md's "Lock Provenance") writes a file, so a display path
# calling it on every render would break that path's read-only contract
# -- <narrate> is what keeps that write scoped to the one call site that
# is actually reaping something.
#
# <project> is optional and forwarded to
# pp_stale_lock_reap_after_wake_periods unchanged; omit it to resolve via
# PGAI_PROJECT_NAME, exactly as that function's own contract documents.
# Resolution degrades to the shipped default of 3 periods (rather than
# failing) when pp_stale_lock_reap_after_wake_periods is not loaded in this
# process -- the case for a caller that sources this file standalone (a
# unit test, a script with no project context), which never had a config
# lookup to make in the first place.
# ---------------------------------------------------------------------------
_pgai_lock_resolve_stale_threshold() {
    local _project="${1:-}"
    local _narrate="${2:-}"
    local _periods=""
    if declare -F pp_stale_lock_reap_after_wake_periods >/dev/null 2>&1; then
        _periods="$(pp_stale_lock_reap_after_wake_periods "${_project}" 2>/dev/null)" || _periods=""
    fi
    [[ "$_periods" =~ ^[0-9]+$ ]] || _periods=3

    local _period_based=$(( _periods * _PGAI_LOCK_WAKE_PERIOD_SECONDS ))
    # Direct call, never `$(...)`: the budget sidecar set is read below.
    _pgai_lock_resolve_chain_budget
    local _floor="$_PGAI_LOCK_BUDGET_FLOOR" _addends
    _addends="$(_pgai_lock_chain_budget_addends)"

    if (( _period_based >= _floor )); then
        _PGAI_LOCK_LAST_THRESHOLD_SECONDS=$_period_based
        _PGAI_LOCK_LAST_THRESHOLD_DERIVATION="configured ${_periods} wake period(s) x ${_PGAI_LOCK_WAKE_PERIOD_SECONDS}s/period = ${_period_based}s, at or above the max-chain-budget floor of ${_floor}s (${_addends})"
    else
        _PGAI_LOCK_LAST_THRESHOLD_SECONDS=$_floor
        _PGAI_LOCK_LAST_THRESHOLD_DERIVATION="max-chain-budget floor enforced at ${_floor}s (${_addends}), above the configured ${_periods} wake period(s) x ${_PGAI_LOCK_WAKE_PERIOD_SECONDS}s/period = ${_period_based}s"
        if [[ "$_narrate" == "narrate" ]] \
            && [[ "$_PGAI_LOCK_FLOOR_NARRATED_DERIVATION" != "$_PGAI_LOCK_LAST_THRESHOLD_DERIVATION" ]]; then
            # CONVERT: quality-class clamp, zero runtime-failure risk -- a
            # configured stale_lock_reap_after_wake_periods that resolves
            # below the max-chain-budget floor is corrected to the floor
            # rather than honored verbatim or refused outright (see this
            # function's own header for the full reasoning). The caller
            # always receives the larger, safer effective value
            # regardless; this narrates why, for the one caller
            # (pgai_lock_acquire's contention classification) that is
            # actually acting on the clamped value rather than merely
            # displaying it.
            # Narrates at most once per process, or again only if the
            # derivation itself changes -- see the
            # _PGAI_LOCK_FLOOR_NARRATED_DERIVATION sidecar's own header
            # above for why a repeat of the SAME derivation never re-fires.
            # CENSUS-ANCHOR: lock-stale-threshold-floor-01
            _PGAI_LOCK_FLOOR_NARRATED_DERIVATION="$_PGAI_LOCK_LAST_THRESHOLD_DERIVATION"
            pgai_emit_error_contract \
                "lock.stale_threshold.floor_enforced" \
                "pgai_lock_stale_age_threshold_seconds: configured stale_lock_reap_after_wake_periods (${_periods} periods = ${_period_based}s) is below the max-chain-budget floor of ${_floor}s (${_addends}) -- a wake chain can legitimately hold this lock for its whole batch, so a live holder is never called overdue below that budget. Clamping the effective threshold to ${_floor}s." \
                "No action needed -- the floor already protects a live chain from being reaped mid-run. To make stale_lock_reap_after_wake_periods itself the effective threshold, raise it (in kanban.cfg or project.cfg's [locks] section) to at least $(( (_floor + _PGAI_LOCK_WAKE_PERIOD_SECONDS - 1) / _PGAI_LOCK_WAKE_PERIOD_SECONDS )) periods." \
                "${_project}"
        fi
    fi
}

# ---------------------------------------------------------------------------
# pgai_lock_stale_age_threshold_seconds [project]
# `$(...)`-safe, side-effect-free public wrapper: calls _pgai_lock_resolve_
# stale_threshold directly with <narrate> omitted (so a clamp, if any, is
# silent -- see that function's own header for why every read-only display
# caller of this wrapper needs that) and echoes the resulting seconds
# value. See "Contention-path staleness reaping" in this file's header for
# what condition this threshold backs; see _pgai_lock_resolve_stale_
# threshold above for the derivation itself.
#
# A caller that is itself ACTING on the clamped value (pgai_lock_acquire's
# own contention classification) must call _pgai_lock_resolve_stale_threshold directly
# with "narrate" instead of this wrapper -- see that function's header.
# ---------------------------------------------------------------------------
pgai_lock_stale_age_threshold_seconds() {
    _pgai_lock_resolve_stale_threshold "${1:-}"
    echo "$_PGAI_LOCK_LAST_THRESHOLD_SECONDS"
}

# ---------------------------------------------------------------------------
# pgai_lock_stale_age_threshold_derivation [project]
# `$(...)`-safe, side-effect-free public wrapper: calls _pgai_lock_resolve_
# stale_threshold directly with <narrate> omitted and echoes the resulting
# one-line derivation string alone -- for a caller (why_details_render_
# locks, team/scripts/lib/why_details.sh) that wants the human-readable
# account without separately fetching and discarding the seconds value
# first. Shared by that caller and pgai_lock_acquire's own overdue-holder
# narration (which calls the resolver directly with "narrate" instead, per
# that function's own header) so both surfaces describe the SAME
# derivation in the SAME words, never two independently-worded copies.
# ---------------------------------------------------------------------------
pgai_lock_stale_age_threshold_derivation() {
    _pgai_lock_resolve_stale_threshold "${1:-}"
    echo "$_PGAI_LOCK_LAST_THRESHOLD_DERIVATION"
}

# ---------------------------------------------------------------------------
# pgai_lock_contention_probe_count <lock_file>
# Echoes how many times pgai_lock_acquire has hit contention on <lock_file>
# in THIS process (see the _PGAI_LOCK_CONTENDED_PROBE_COUNT sidecar's own
# header above) -- 0 when <lock_file> was never contended this process,
# regardless of whether the dual-channel narration itself fired (it fires
# only for the first probe; this count keeps rising for every probe after
# that). A caller that stops retrying (wake_driver.sh's first-pass defer,
# wake_common.sh's end-of-iteration retry outcome) reads this to name the
# total probe count in its own closing summary line, e.g. "still contended
# after 3 probe(s), deferring to the next wake firing" -- this rate limit's
# "ONE narration ... with a closing summary" requirement.
# ---------------------------------------------------------------------------
pgai_lock_contention_probe_count() {
    local _lock_file="${1:-}"
    echo "${_PGAI_LOCK_CONTENDED_PROBE_COUNT[$_lock_file]:-0}"
}

# ---------------------------------------------------------------------------
# pgai_lock_files_for_project <project>
# Echo, one per line, every lock file under $KANBAN_ROOT/locks/ whose name
# identifies <project> -- both naming schemes in the tree today
# (pp_repo_lock_path's repo-wake-<repo_id>.lock and
# overwatch_acquire_firing_lock's overwatch-<project>.lock), matched by a
# "-<name>.lock" suffix. Its Python form,
# pgai_agent_kanban.ops.locks.project_lock_files, is what why.sh's lock
# pane and STUCK-LOCK correlation (pgai_agent_kanban.ops.verdict) read; a
# unit test holds the two to the same answer on the same locks/ directory.
#
# KANBAN_ROOT must be exported. Echoes nothing (not an error) when locks/
# is absent or no lock file matches.
# ---------------------------------------------------------------------------
pgai_lock_files_for_project() {
    local project="$1"
    local locks_dir="${KANBAN_ROOT}/locks"
    [[ -d "${locks_dir}" ]] || return 0

    local repo_id
    repo_id="$(printf '%s' "${project}" | tr '/' '_' | tr -cd '[:alnum:]_-')"

    local f base
    for f in "${locks_dir}"/*.lock; do
        [[ -e "${f}" ]] || continue
        base="$(basename "${f}")"
        if [[ "${base}" == *"-${project}.lock" || "${base}" == *"-${repo_id}.lock" ]]; then
            printf '%s\n' "${f}"
        fi
    done
    return 0
}

# ---------------------------------------------------------------------------
# pgai_lock_read_provenance <lock_file>
# Echo the raw provenance record text of <lock_file> (empty if the file is
# empty, absent, or unreadable). Public reader for callers that want to
# report or reason about who holds a lock without acquiring it themselves
# -- e.g. a contention-message caller that wants the fields in its own
# wording, or a diagnostic sweep that wants to know whether a lock file's
# recorded pid is still alive.
# ---------------------------------------------------------------------------
pgai_lock_read_provenance() {
    local lock_file="$1"
    cat "$lock_file" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _pgai_lock_inode_holder_pids <lock_file> <exclude_pid> [close_fd]
# Echo, space-separated in ascending order, the pids of processes that have
# the file currently at <lock_file> open (any descriptor on the same device
# and inode), read from /proc/<pid>/fd. Best-effort: processes this account
# cannot inspect are not listed, and a host without /proc prints nothing.
# <exclude_pid> (the caller's own $BASHPID, read BEFORE any $(...) around
# this call -- inside one it names the subshell) is left out, and [close_fd], a
# descriptor the caller holds on the same file, is closed in this
# function's own subshell first so the find child does not inherit it and
# list itself. Used only to NAME, in pgai_lock_acquire's orphan-holder
# narration, who actually holds a lock whose recorded pid is dead.
# ---------------------------------------------------------------------------
_pgai_lock_inode_holder_pids() {
    local lock_file="$1" exclude_pid="$2" close_fd="${3:-}"
    if [[ "$close_fd" =~ ^[0-9]+$ ]]; then
        { exec {close_fd}<&-; } 2>/dev/null || true
    fi
    local _entry _pid _out=""
    local -A _seen=()
    while IFS= read -r _entry; do
        _pid="${_entry#/proc/}"
        _pid="${_pid%%/*}"
        [[ "$_pid" =~ ^[0-9]+$ ]] || continue
        [[ "$_pid" == "$exclude_pid" || "$_pid" == "$BASHPID" ]] && continue
        [[ -n "${_seen[$_pid]:-}" ]] && continue
        _seen[$_pid]=1
        _out+="${_pid}"$'\n'
    done < <(find -L /proc/[0-9]*/fd -mindepth 1 -maxdepth 1 -samefile "$lock_file" 2>/dev/null || true)
    printf '%s' "$_out" | sort -n | paste -sd' ' -
}

# ---------------------------------------------------------------------------
# pgai_lock_acquire <lock_file> <holder> <pid> <op> <task> <out_fd_var>
#
# Take a non-blocking exclusive flock on <lock_file> (created if missing)
# and record who holds it. All four of <holder> <pid> <op> <task> are
# mandatory and must be non-blank -- see the refusal below.
#
# The record is staged in <lock_file>.tmp.<pid>, flocked there, and then
# linked or renamed onto <lock_file> -- see "Write-ordering invariant" in
# this file's header -- so the file at <lock_file> carries this caller's
# complete provenance from the first instant it names this caller's
# inode. Nothing is ever written into, or truncated at, the lock path.
#
# On success:
#   - <lock_file> holds exactly the four caller fields plus acquired_at, the
#     acquisition time in ISO-8601 UTC (see file header)
#   - the open file descriptor number is stored into the CALLER's variable
#     named by <out_fd_var> (the caller must `local`-declare that variable
#     name in its own scope before calling this function -- bash's dynamic
#     scoping is what lets `printf -v "$out_fd_var" ...` reach it)
#   - returns 0
#
# On contention (another process already holds the flock):
#   - prints a message to stderr naming the current holder/pid/op/task read
#     from the file's existing provenance (or "(no provenance recorded)"
#     when the file predates this helper or its holder does not use it,
#     with a note when that file is too fresh to reap)
#   - closes the fds it opened for the attempt and removes its staging
#     file; nothing is left open or behind
#   - returns 1
#
# On incomplete provenance (any of the four fields blank):
#   - refuses dual-channel (see the CENSUS-ANCHOR comment at the refusal
#     site below) and takes no lock at all -- nothing is opened, nothing
#     is written
#   - returns 2
#
# On contention where the existing file carries no provenance and is not
# fresh (see "Contention-path staleness reaping" in this file's header):
# reaps the stale lock (narrated dual-channel to stderr, naming the evidence) and
# retries the acquisition once against the now-clear path, returning
# whatever that retry returns. A live, well-provenanced holder is never
# reaped, whatever its age -- one whose lock has aged past the stale-lock
# threshold is additionally narrated as overdue (lock.provenance.holder_
# overdue, once per lock per process) and then deferred to exactly like
# any other live holder. A provenance-less file younger than
# _PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS is deferred to, not reaped.
# A record naming a dead pid while the flock is still held is deferred to
# as well, narrated once as lock.provenance.orphan_holder naming the pids
# that hold the file open, and returns 1 like any contention.
#
# A flock won on an inode the path no longer names, a path that vanished
# or changed between open and flock, and a reap whose target was replaced
# before the unlink are each retried against the current path, at most
# _PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS times per call -- see "Won on an orphan"
# in this file's header.
# ---------------------------------------------------------------------------
pgai_lock_acquire() {
    local lock_file="$1" holder="$2" pid="$3" op="$4" task="$5" out_fd_var="$6"

    if [[ -z "$lock_file" || -z "$out_fd_var" ]]; then
        echo "lock_provenance.sh: pgai_lock_acquire: lock_file and out_fd_var arguments are required" >&2
        return 2
    fi

    local -a missing=()
    [[ -z "$holder" ]] && missing+=("holder")
    [[ -z "$pid" ]] && missing+=("pid")
    [[ -z "$op" ]] && missing+=("op")
    [[ -z "$task" ]] && missing+=("task")
    if [[ ${#missing[@]} -gt 0 ]]; then
        local missing_list
        missing_list="$(IFS=', '; echo "${missing[*]}")"
        pgai_emit_error_contract \
            "lock.provenance.incomplete" \
            "pgai_lock_acquire refused: acquiring ${lock_file} is missing required provenance field(s): ${missing_list}. holder, pid, op, and task must all be non-blank." \
            "Pass a non-blank value for every field named above. A caller with a legitimate reason to have no task to record (for example an OVERWATCH sweep) should pass the literal string 'none' rather than leaving the field blank."
        # BLOCK-WORTHY: a lock this framework holds with a blank provenance
        # field is exactly the undiagnosable-lock failure mode this helper
        # exists to prevent -- a wedged lock could not be diagnosed and a
        # stale one could not be told from a live one. Refusing here is an
        # invariant-by-design stop, not a quality finding (team/SOP-core.md,
        # "Gates and Refusals").
        # CENSUS-ANCHOR: lock-provenance-01
        return 2
    fi

    local lock_dir
    lock_dir="$(dirname "$lock_file")"
    mkdir -p "$lock_dir" 2>/dev/null || {
        echo "lock_provenance.sh: pgai_lock_acquire: cannot create lock directory: ${lock_dir}" >&2
        return 2
    }

    # Staging litter: a caller that died between staging its record and
    # linking it onto the path leaves <lock_file>.tmp.<pid> behind. Such a
    # file is never at the lock path, so it blocks nothing; it is removed
    # here once its pid is gone so the lock directory does not accumulate
    # them. compgen rather than a bare glob so a caller's failglob/nullglob
    # settings cannot turn "no litter" into an error.
    local _stale_tmp _stale_pid
    while IFS= read -r _stale_tmp; do
        [[ -n "$_stale_tmp" && -e "$_stale_tmp" ]] || continue
        _stale_pid="${_stale_tmp##*.}"
        [[ "$_stale_pid" =~ ^[0-9]+$ ]] || continue
        [[ "$_stale_pid" == "$BASHPID" ]] && continue
        kill -0 "$_stale_pid" 2>/dev/null && continue
        rm -f -- "$_stale_tmp" 2>/dev/null || true
    done < <(compgen -G "${lock_file}.tmp.*" 2>/dev/null || true)

    # Stage the record in a private sibling file and take the flock on
    # THAT inode before it can be reached by the lock path. Truncating on
    # open is fine here -- this file is this caller's alone -- and is
    # exactly what must never happen at the lock path itself (see the
    # write-ordering invariant in this file's header).
    # NOTE ON REDIRECTION SCOPE: `exec {fd}>...` with no command word applies
    # its redirections to the CURRENT SHELL, not just to one statement --
    # that is what makes the fd persist after this function returns.  For
    # the SAME reason, a bare `exec {fd}>... 2>/dev/null` would ALSO
    # permanently redirect this shell's stderr to /dev/null from that line
    # onward, silently swallowing every later diagnostic (including this
    # function's own contention/refusal messages). Wrapping the exec in a
    # `{ ...; }` group scopes the 2>/dev/null redirect to the group only --
    # the fd it opens still escapes the group (exec's whole point), but
    # stderr is restored once the group ends.
    local _tmp="${lock_file}.tmp.${BASHPID}"
    local _fd
    { exec {_fd}>"$_tmp"; } 2>/dev/null || {
        echo "lock_provenance.sh: pgai_lock_acquire: cannot create provenance staging file: ${_tmp}" >&2
        return 2
    }
    # acquired_at rides in the same single staged write as the four caller
    # fields, so the record that appears at the lock path is complete from
    # its first instant (write-ordering invariant above).
    local _acquired_at
    _acquired_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if ! printf 'holder=%s\npid=%s\nop=%s\ntask=%s\nacquired_at=%s\n' "$holder" "$pid" "$op" "$task" "$_acquired_at" 1>&"$_fd" 2>/dev/null; then
        echo "lock_provenance.sh: pgai_lock_acquire: cannot write provenance to staging file: ${_tmp}" >&2
        { exec {_fd}>&-; } 2>/dev/null || true
        rm -f -- "$_tmp" 2>/dev/null || true
        return 2
    fi
    if ! flock -n "$_fd" 2>/dev/null; then
        echo "lock_provenance.sh: pgai_lock_acquire: cannot flock the staging file ${_tmp} this caller just created -- does the filesystem under ${lock_dir} support flock?" >&2
        { exec {_fd}>&-; } 2>/dev/null || true
        rm -f -- "$_tmp" 2>/dev/null || true
        return 2
    fi

    local _attempt=0 _reaped=false
    local _pfd _existing _existing_desc _lock_name
    _lock_name="$(basename "$lock_file" .lock)"
    while :; do
        _attempt=$(( _attempt + 1 ))
        if (( _attempt > _PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS )); then
            echo "lock_provenance.sh: pgai_lock_acquire: declining ${lock_file} after ${_PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS} attempts -- the file at that path was replaced between open and flock on every attempt, so another process is acquiring or releasing it continuously; treating this as contention. Retry on the next wake, and if it persists, list the lock's holders with: team/scripts/release-locks.sh" >&2
            { exec {_fd}>&-; } 2>/dev/null || true
            rm -f -- "$_tmp" 2>/dev/null || true
            return 1
        fi

        # Atomic create. link(2) fails with EEXIST rather than replacing,
        # so when nothing sits at the path the staged, already-flocked,
        # already-provenanced inode becomes the lock in one step and the
        # staging name is dropped.
        if ln -- "$_tmp" "$lock_file" 2>/dev/null; then
            rm -f -- "$_tmp" 2>/dev/null || true
            break
        fi

        # Something sits at the path. Open it READ-ONLY -- never truncating,
        # never writing -- and contend for its flock.
        if ! { exec {_pfd}<"$lock_file"; } 2>/dev/null; then
            if [[ -e "$lock_file" ]]; then
                echo "lock_provenance.sh: pgai_lock_acquire: cannot open lock file: ${lock_file}" >&2
                { exec {_fd}>&-; } 2>/dev/null || true
                rm -f -- "$_tmp" 2>/dev/null || true
                return 2
            fi
            # Vanished between the link attempt and the open: a release
            # just finished. Link again.
            continue
        fi

        if flock -n "$_pfd" 2>/dev/null; then
            # Won -- but on the inode the path still names? A release or a
            # reap may have unlinked it, or another acquisition renamed over
            # it, between this caller's open and its flock. A win on an
            # orphaned inode excludes nobody, so it is closed and the
            # attempt repeated (see "Won on an orphan" in the header).
            if [[ "$(_pgai_lock_path_verdict "$lock_file" "$_pfd")" == "other" ]]; then
                { exec {_pfd}>&-; } 2>/dev/null || true
                continue
            fi

            # This caller holds the flock on the file at the path. Read
            # whatever provenance was left behind before replacing it --
            # purely to log a diagnostic when it names a pid that is no
            # longer running (see "Crash recovery" in the file header; this
            # never affects the return code).
            local _old_content _old_pid
            _old_content="$(pgai_lock_read_provenance "$lock_file")"
            _old_pid="$(_pgai_lock_field "$_old_content" pid)"
            if [[ -n "$_old_pid" ]] && ! kill -0 "$_old_pid" 2>/dev/null; then
                echo "lock_provenance.sh: pgai_lock_acquire: previous provenance in ${lock_file} named pid ${_old_pid}, which is no longer running; the lock itself was already free (flock excludes, not this record) -- replacing it with fresh provenance." >&2
            fi

            # Recovery sweep: a pre-existing file with no provenance at all
            # whose flock nobody held is a lock file left behind without a
            # record -- narrate the replacement rather than silently paving
            # over it.
            if [[ -z "$_old_content" ]]; then
                echo "lock_provenance.sh: pgai_lock_acquire: recovery sweep: ${lock_file} existed with no provenance recorded and nobody held its flock -- a lock file left behind without a record (a writer outside this helper, or an operator-triggered release that swapped an empty file in for a dead holder); replacing it with this acquisition's record." >&2
            fi

            # rename(2): the staged, flocked, provenanced inode replaces the
            # previous file at the path in one step. The previous inode is
            # now unreachable by path, so this caller's flock on it is
            # dropped; a waiter still holding it open wins nothing real
            # (its own path check sends it round again).
            if ! mv -f -- "$_tmp" "$lock_file" 2>/dev/null; then
                echo "lock_provenance.sh: pgai_lock_acquire: cannot move staged provenance ${_tmp} onto ${lock_file}" >&2
                { exec {_pfd}>&-; } 2>/dev/null || true
                { exec {_fd}>&-; } 2>/dev/null || true
                rm -f -- "$_tmp" 2>/dev/null || true
                return 2
            fi
            { exec {_pfd}>&-; } 2>/dev/null || true
            break
        fi

        # Lost the flock on the inode this caller opened. If the path no
        # longer names that inode, its holder is mid-release or it was
        # replaced; whatever is at the path now is what to contend for.
        if [[ "$(_pgai_lock_path_verdict "$lock_file" "$_pfd")" == "other" ]]; then
            { exec {_pfd}>&-; } 2>/dev/null || true
            continue
        fi

        _existing="$(pgai_lock_read_provenance "$lock_file")"
        _existing_desc="$(pgai_lock_describe_provenance "$_existing")"

        # Staleness reaping (see "Contention-path staleness reaping" in this
        # file's header): before deferring, test the one reap condition
        # (no provenance, not fresh) against whatever the losing side's
        # own provenance shows, and read the lock's age for the overdue
        # narration. A live, well-provenanced holder does not trip it and
        # falls through to the unchanged contended refusal below --
        # narrated as overdue first when its lock has aged past the
        # threshold, never reaped for it; a record naming a dead pid is
        # narrated as orphan-held and deferred to the same way.
        local _liveness_verdict _lock_age_secs=0 _age_threshold _age_derivation _reap_reason="" _holder_overdue=false _fresh_unprovenanced=false _orphan_held=false
        _liveness_verdict="$(pgai_lock_pid_liveness_verdict "$_existing")"
        if [[ -e "$lock_file" ]]; then
            local _lock_mtime _now_epoch
            _lock_mtime="$(stat -c '%Y' "$lock_file" 2>/dev/null || true)"
            _now_epoch="$(date +%s)"
            if [[ "$_lock_mtime" =~ ^[0-9]+$ ]]; then
                _lock_age_secs=$(( _now_epoch - _lock_mtime ))
                (( _lock_age_secs < 0 )) && _lock_age_secs=0
            fi
        fi
        # Direct call (never through `$(...)`): _pgai_lock_resolve_stale_
        # threshold's own header explains why a subshell would drop the
        # derivation sidecar. Calling it once here, rather than through the
        # $(...)-safe seconds/derivation wrappers separately, is also what
        # keeps a single contention decision from double-firing the
        # resolver's own clamp narration to stderr. "narrate" is passed
        # explicitly: this call site is the one place that ACTS on a
        # clamped value (it classifies the holder against it below), unlike the two public
        # wrappers' own read-only callers, which must stay silent.
        _pgai_lock_resolve_stale_threshold "" narrate
        _age_threshold="$_PGAI_LOCK_LAST_THRESHOLD_SECONDS"
        _age_derivation="$_PGAI_LOCK_LAST_THRESHOLD_DERIVATION"

        case "$_liveness_verdict" in
            stale)
                # The flock is held (this caller just lost it) yet the
                # recorded pid is dead: an orphan holds it, never a stale
                # lock (header, "Contention-path staleness reaping").
                # Narrated below and deferred to, never unlinked.
                _orphan_held=true
                ;;
            unparseable)
                # Condition 2 needs both halves: no record AND not fresh. A
                # provenance-less file younger than the window is a writer
                # mid-flight until proven otherwise (header, condition 2).
                if (( _lock_age_secs >= _PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS )); then
                    _reap_reason="no-provenance"
                else
                    _fresh_unprovenanced=true
                fi
                ;;
        esac
        if [[ -z "$_reap_reason" && "$_orphan_held" == false && "$_lock_age_secs" -ge "$_age_threshold" ]]; then
            # Live pid, lock aged past the holder's whole budget: evidence
            # of a wedged holder, never grounds to reap it (see
            # "Contention-path staleness reaping" in this file's header).
            # Narrated below, then deferred to like any live holder.
            _holder_overdue=true
        fi

        if [[ -n "$_reap_reason" && "$_reaped" == false ]]; then
            # One reap per call. Re-check that the path still names the
            # inode this caller classified before unlinking it: a file
            # replaced in the meantime is not the file that was judged.
            if [[ "$(_pgai_lock_path_verdict "$lock_file" "$_pfd")" == "other" ]]; then
                { exec {_pfd}>&-; } 2>/dev/null || true
                continue
            fi
            _reaped=true
            local _reap_age_human _reap_key _reap_message
            _reap_age_human="$(pgai_lock_format_age_seconds "$_lock_age_secs")"
            case "$_reap_reason" in
                no-provenance)
                    _reap_key="lock.provenance.stale_reaped.no_provenance"
                    _reap_message="pgai_lock_acquire reaped ${lock_file}: no provenance recorded at all and the file is ${_reap_age_human} old, past the ${_PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS}s freshness window -- a lock this framework holds carries its provenance record from the instant it appears; a provenance-less lock this old is the on-disk evidence of a non-atomic writer outside this helper or a holder that never used it, either of which makes the record untrustworthy rather than a barrier to reacquisition."
                    ;;
            esac
            pgai_emit_error_contract \
                "$_reap_key" \
                "$_reap_message" \
                "No action needed -- the waiter reaped the stale lock automatically and proceeded with its own acquisition. If stale locks recur often for this path, inspect the holder's exit behavior; team/scripts/release-locks.sh lists every lock file with its provenance and staleness verdict."
            rm -f -- "$lock_file" 2>/dev/null || true
            { exec {_pfd}>&-; } 2>/dev/null || true
            continue
        fi

        if [[ "$_orphan_held" == true && -z "${_PGAI_LOCK_ORPHAN_NARRATED["$lock_file"]:-}" ]]; then
            _PGAI_LOCK_ORPHAN_NARRATED["$lock_file"]=1
            local _orphan_pid _orphan_held_phrase _orphan_pids _orphan_named _orphan_remedy _orphan_self="$BASHPID"
            _orphan_pid="$(_pgai_lock_field "$_existing" pid)"
            _orphan_held_phrase="$(_pgai_lock_held_phrase "$_existing")"
            # This shell's own pid is read before the command substitution:
            # inside $(...) BASHPID already names the subshell.
            _orphan_pids="$(_pgai_lock_inode_holder_pids "$lock_file" "$_orphan_self" "$_pfd")"
            if [[ -n "$_orphan_pids" ]]; then
                _orphan_named="pids ${_orphan_pids}"
                _orphan_remedy="If those processes are leftovers of a stopped chain rather than live work, stop them: kill -TERM ${_orphan_pids} -- or let team/scripts/release-locks.sh --release ${_lock_name} --force signal them; the next acquisition then takes the lock."
            else
                _orphan_named="pids not visible to this account"
                _orphan_remedy="List the processes holding it with: find -L /proc/[0-9]*/fd -maxdepth 1 -samefile ${lock_file} 2>/dev/null -- stop them if they are leftovers of a stopped chain, or run team/scripts/release-locks.sh --release ${_lock_name} --force; the next acquisition then takes the lock."
            fi
            # CONVERT: quality-class narration with zero runtime-failure
            # risk -- nothing is refused here that the contended refusal
            # below would not refuse anyway, and nothing on disk is
            # touched. The flock is held by a process the record does not
            # name; unlinking the file would not stop it, only seat a
            # second holder beside it, so the waiter names who really
            # holds it and defers.
            pgai_emit_error_contract \
                "lock.provenance.orphan_holder" \
                "pgai_lock_acquire is deferring on ${lock_file}: its record names pid ${_orphan_pid}, which is not running (${_orphan_held_phrase}), yet the lock's flock is still held -- by ${_orphan_named}. A process that inherited the holder's lock descriptor, a holder in another pid namespace, or an acquirer between its flock and its rename holds it; the file is left in place because removing it would only let a second holder in beside them -- ${_existing_desc}." \
                "$_orphan_remedy"
        fi

        if [[ "$_holder_overdue" == true && -z "${_PGAI_LOCK_OVERDUE_NARRATED["$lock_file"]:-}" ]]; then
            _PGAI_LOCK_OVERDUE_NARRATED["$lock_file"]=1
            local _overdue_pid _overdue_held _overdue_threshold_human
            _overdue_pid="$(_pgai_lock_field "$_existing" pid)"
            _overdue_held="$(_pgai_lock_held_phrase "$_existing")"
            _overdue_threshold_human="$(pgai_lock_format_age_seconds "$_age_threshold")"
            # CONVERT: quality-class narration with zero runtime-failure
            # risk -- nothing is refused here that the ordinary contended
            # deferral below would not refuse anyway, and nothing on disk
            # is touched. A live holder whose lock has aged past its whole
            # budget is evidence of a wedged chain, which an operator
            # resolves by signalling it (the remedy); deleting its file
            # would only seat a second chain beside it.
            # CENSUS-ANCHOR: lock-holder-overdue-01
            pgai_emit_error_contract \
                "lock.provenance.holder_overdue" \
                "pgai_lock_acquire is deferring to a holder that looks wedged: ${lock_file} (${_overdue_held}) has gone unrefreshed for longer than the stale-lock threshold of ${_overdue_threshold_human} (derivation: ${_age_derivation}), yet its recorded holder pid ${_overdue_pid} still answers kill -0 -- ${_existing_desc}. A lock whose holder is alive is never deleted on age alone: unlinking it would not stop that holder, only let a second chain in beside it. A running chain refreshes its lock while a task runs, so this holder has stopped refreshing or is running with the per-task timeout disabled." \
                "If the holder is wedged rather than doing real work, have it release its own lock: team/scripts/release-locks.sh --release ${_lock_name} --force (sends SIGTERM to pid ${_overdue_pid}, whose exit path releases the lock). A holder that has actually died is reaped automatically on the next acquisition."
        fi

        # A fresh, provenance-less file is named as such in the contention
        # narration so the reader knows why it was not reaped.
        local _contended_detail=""
        if [[ "$_fresh_unprovenanced" == true ]]; then
            _contended_detail=" -- the file carries no provenance record yet but was modified ${_lock_age_secs}s ago, inside the ${_PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS}s freshness window, so it is treated as a holder whose record has not appeared rather than reaped; a provenance-less file older than that window is reaped on the next probe"
        fi

        # Narration-rate memoization: every probe against
        # this SAME lock_file in this process increments the counter, but
        # the dual-channel narration itself fires only for the first --
        # "ONE narration per lock per wake pass, never per retry tick". A
        # caller that gives up (wake_driver.sh / wake_common.sh) reads
        # pgai_lock_contention_probe_count below to report its own closing
        # summary once it stops retrying, so the total probe count is never
        # lost -- only the per-probe repeat of this block is.
        _PGAI_LOCK_CONTENDED_PROBE_COUNT["$lock_file"]=$(( ${_PGAI_LOCK_CONTENDED_PROBE_COUNT["$lock_file"]:-0} + 1 ))
        if [[ -z "${_PGAI_LOCK_CONTENDED_NARRATED["$lock_file"]:-}" ]]; then
            _PGAI_LOCK_CONTENDED_NARRATED["$lock_file"]=1
            pgai_emit_error_contract \
                "lock.provenance.contended" \
                "pgai_lock_acquire could not take ${lock_file}: it is already held by ${_existing_desc}${_contended_detail}" \
                "If the holder is still legitimately working, wait and retry -- most callers of this lock already retry automatically. If it looks stuck (the recorded holder pid is dead or wedged), inspect and clear it with: team/scripts/release-locks.sh --release ${_lock_name} (add --force only if the holder pid is still alive and you are certain it should be released)."
        fi
        # BLOCK-WORTHY: invariant-by-design — the flock this lock file backs
        # exists to serialize exclusive access to shared project state; a
        # caller that cannot take it must not proceed as though it had, so
        # this is refused rather than silently retried in place (the same
        # doctrine family as lock-provenance-01's incomplete-provenance
        # refusal above, for the sibling "cannot safely proceed" case). A
        # holder that trips no reap condition above -- every live
        # holder, overdue or not, every orphan-held lock whose recorded pid
        # is dead, and every fresh provenance-less file --
        # (or already survived one bounded reap-and-retry this call)
        # reaches this same refusal exactly as before this file's
        # staleness reaper existed.
        # CENSUS-ANCHOR: lock-provenance-contended-01
        { exec {_pfd}>&-; } 2>/dev/null || true
        { exec {_fd}>&-; } 2>/dev/null || true
        rm -f -- "$_tmp" 2>/dev/null || true
        return 1
    done

    printf -v "$out_fd_var" '%s' "$_fd"
    _PGAI_LOCK_HELD_FDS["$lock_file"]="$_fd"
    _PGAI_LOCK_HELD_PIDS["$lock_file"]="$pid"
    return 0
}

# ---------------------------------------------------------------------------
# pgai_lock_wait_now / pgai_lock_wait_pause <seconds>
# The clock and the pause pgai_lock_acquire_waiting (below) reads: whole
# seconds since the epoch, and a plain sleep. Each is one function so a test
# can substitute a controlled clock and an instant pause by redefining them
# after sourcing this file -- a wait measured in minutes is then exercised
# without a real minute passing. A caller that budgets its own wait around
# pgai_lock_acquire_waiting (the wake chain's lock wait,
# team/scripts/lib/wake_lock_wait.sh) reads the same clock, so the budget
# and the wait it bounds can never disagree.
# ---------------------------------------------------------------------------
pgai_lock_wait_now() {
    date +%s
}

pgai_lock_wait_pause() {
    sleep "$1"
}

# ---------------------------------------------------------------------------
# pgai_lock_acquire_waiting <wait_seconds> <lock_file> <holder> <pid> <op> <task> <out_fd_var>
#
# pgai_lock_acquire, retried on contention with exponential backoff for up
# to <wait_seconds> seconds in total. Every attempt is an ordinary
# pgai_lock_acquire call -- same provenance record, same reaping, same
# once-per-process contention narration -- so a waiter that eventually wins
# holds exactly the lock a first-try winner would, and one that gives up
# leaves exactly what a single contended attempt leaves: nothing.
#
# <wait_seconds> is a whole number of seconds; 0 (or anything that is not
# a whole number) makes this a single attempt, identical to calling
# pgai_lock_acquire directly. The pause between attempts starts at
# _PGAI_LOCK_WAIT_FIRST_BACKOFF_SECONDS, doubles each time, is capped at
# _PGAI_LOCK_WAIT_MAX_BACKOFF_SECONDS, and is never longer than the time
# left in the budget, so the last attempt lands at (about) the deadline.
# Time is read through pgai_lock_wait_now and paused through
# pgai_lock_wait_pause (above).
#
# Only contention (pgai_lock_acquire's return 1) is retried. A refusal
# (return 2: incomplete provenance, an unusable lock directory) returns at
# once -- waiting cannot fix it.
#
# Optional keep-waiting hook: when the caller's scope sets
# PGAI_LOCK_WAIT_WHILE to the name of a function, that function is called
# as `<function> <lock_file>` after every contended attempt that still has
# budget left, just before the pause. A non-zero return ends the wait at
# once with return 1 (still contended) -- how a caller waits only while the
# holder is one worth waiting for, or stops waiting when an operator asks
# it to. Unset or empty, every contended attempt with budget left is
# followed by a pause, exactly as without the hook.
#
# <out_fd_var> is handed to pgai_lock_acquire unchanged, so the caller
# declares it `local` in its own scope exactly as for pgai_lock_acquire;
# every local here is underscore-prefixed so it cannot shadow that name.
#
# Returns pgai_lock_acquire's own code from the last attempt: 0 acquired,
# 1 still contended when the budget ran out (or the hook ended the wait),
# 2 refused.
# ---------------------------------------------------------------------------
pgai_lock_acquire_waiting() {
    local _plaw_wait="$1"
    shift
    [[ "$_plaw_wait" =~ ^[0-9]+$ ]] || _plaw_wait=0

    local _plaw_deadline=$(( $(pgai_lock_wait_now) + 10#$_plaw_wait ))
    local _plaw_pause="$_PGAI_LOCK_WAIT_FIRST_BACKOFF_SECONDS"
    local _plaw_hook="${PGAI_LOCK_WAIT_WHILE:-}"
    local _plaw_rc _plaw_left
    while true; do
        _plaw_rc=0
        pgai_lock_acquire "$@" || _plaw_rc=$?
        [[ $_plaw_rc -eq 1 ]] || return "$_plaw_rc"

        _plaw_left=$(( _plaw_deadline - $(pgai_lock_wait_now) ))
        (( _plaw_left > 0 )) || return 1
        if [[ -n "$_plaw_hook" ]] && ! "$_plaw_hook" "$1"; then
            return 1
        fi
        (( _plaw_pause > _plaw_left )) && _plaw_pause="$_plaw_left"
        pgai_lock_wait_pause "$_plaw_pause"
        _plaw_pause=$(( _plaw_pause * 2 ))
        (( _plaw_pause > _PGAI_LOCK_WAIT_MAX_BACKOFF_SECONDS )) && _plaw_pause="$_PGAI_LOCK_WAIT_MAX_BACKOFF_SECONDS"
    done
}

# ---------------------------------------------------------------------------
# _pgai_lock_path_verdict <lock_file> <fd>
# Echo one word saying whether the file currently at <lock_file> is the
# very file <fd> holds the flock on:
#   own      -- same inode: the path is still this process's own lock
#   other    -- a different inode sits at the path (another process reaped
#               this lock and reacquired it, creating a new file there), or
#               nothing sits at the path at all
#   unknown  -- could not be determined (no /proc, stat unavailable)
# Internal helper for pgai_lock_release's ownership check. The stat that
# reads the held inode runs as a child process: it inherits <fd>, so
# /proc/self/fd/<fd> in that child names the same open file description
# this shell holds -- including after an unlink, when the path itself no
# longer does.
# ---------------------------------------------------------------------------
_pgai_lock_path_verdict() {
    local lock_file="$1" fd="$2"
    local _held_ino _path_ino
    _held_ino="$(stat -L -c '%i' "/proc/self/fd/${fd}" 2>/dev/null || true)"
    if [[ ! "$_held_ino" =~ ^[0-9]+$ ]]; then
        echo "unknown"
        return 0
    fi
    _path_ino="$(stat -c '%i' "$lock_file" 2>/dev/null || true)"
    if [[ "$_path_ino" == "$_held_ino" ]]; then
        echo "own"
    else
        echo "other"
    fi
}

# ---------------------------------------------------------------------------
# _pgai_lock_ownership_verdict <lock_file> <fd>
# Echo "own" or "other": is the file at <lock_file> still this process's
# own lock? _pgai_lock_path_verdict's inode comparison decides when it
# can; when inode evidence is unavailable ("unknown") this falls back to
# comparing the on-disk record's pid with the pid this process recorded
# at acquisition -- a record naming a different pid is somebody else's,
# while an absent or pid-less record cannot contradict ownership and reads
# as "own". Shared by pgai_lock_release's ownership check and
# pgai_lock_refresh_held_mtime, so the two never judge ownership by
# different rules. Internal helper.
# ---------------------------------------------------------------------------
_pgai_lock_ownership_verdict() {
    local lock_file="$1" fd="$2"
    local _verdict
    _verdict="$(_pgai_lock_path_verdict "$lock_file" "$fd")"
    if [[ "$_verdict" == "unknown" ]]; then
        local _recorded_pid _on_disk_pid
        _recorded_pid="${_PGAI_LOCK_HELD_PIDS[$lock_file]:-}"
        _on_disk_pid="$(_pgai_lock_field "$(pgai_lock_read_provenance "$lock_file")" pid)"
        if [[ -n "$_on_disk_pid" && -n "$_recorded_pid" && "$_on_disk_pid" != "$_recorded_pid" ]]; then
            _verdict="other"
        else
            _verdict="own"
        fi
    fi
    echo "$_verdict"
}

# ---------------------------------------------------------------------------
# pgai_lock_release <lock_file> <fd>
#
# Drop a lock acquired by pgai_lock_acquire, atomically from an observer's
# point of view: unlink the lock file, then close the file descriptor.
# Safe to call with an empty/unset <fd> (no-op, returns 0) so callers can
# release unconditionally in cleanup paths without first checking whether
# acquisition ever succeeded. Also safe to call twice for the same
# lock_file (e.g. once explicitly and once via the
# pgai_lock_release_all_held trap/finally sweep on the way out) -- the
# first call drops this process's bookkeeping for the path, so the second
# finds nothing of its own there and touches nothing (see the ownership
# check below).
#
# Ownership-checked ("release only what you still hold" -- see "Hold
# discipline and ownership-checked release" in this file's header): the
# unlink below runs only when the file at <lock_file> is the very inode
# <fd> holds the flock on. When another process has since reaped this
# lock and reacquired it -- a different file, or none, now sits at the
# path -- this call degrades to pgai_lock_abandon (close own fd, drop
# bookkeeping, touch nothing) and narrates dual-channel, because
# unlinking that file would free the lock out from under its live holder.
# Returns 0 either way.
#
# One on-disk step, performed WHILE <fd> still holds the flock: remove the
# lock file from disk. There is deliberately no truncate before it -- see
# "Truncate-free release" in this file's header for the race an emptied-
# but-linked file used to open. A caller that opens this same path next
# (pgai_lock_acquire's read-only open, or its link of a staged record)
# is unaffected by the unlink: removing a directory entry never disturbs
# a file descriptor another process already has open on the old inode,
# and a waiter left holding that orphaned inode detects it and retries
# (see "Won on an orphan" in the header). The record goes with the file,
# so the release-side mirror of the acquire-side law still holds: no
# provenance-less lock file survives a clean release.
#
# A process killed after the unlink but before closing <fd> has already
# let go of the path; the kernel closes <fd> -- and so the flock -- on its
# exit. Nothing is left on disk in either order.
# ---------------------------------------------------------------------------
pgai_lock_release() {
    local lock_file="$1" fd="$2"

    if [[ -z "$fd" ]]; then
        return 0
    fi

    if [[ -n "$lock_file" ]]; then
        # Ownership check, three questions in order:
        #   1. Does this process's own bookkeeping still say it holds
        #      <lock_file>? If not (already released, or abandoned after a
        #      reverify loss), nothing of ours is at the path: close the fd
        #      if it is still open and touch nothing.
        #   2. Is the file at the path the very file <fd> holds the flock
        #      on (same inode)? Then this is the ordinary release.
        #   3. Otherwise another process reaped this lock and reacquired
        #      it, so a NEW file (or none) sits at the path: abandon and
        #      narrate instead of unlinking the new holder's record.
        if [[ -z "${_PGAI_LOCK_HELD_FDS[$lock_file]+set}" ]]; then
            { exec {fd}>&-; } 2>/dev/null || true
            return 0
        fi
        local _verdict
        _verdict="$(_pgai_lock_ownership_verdict "$lock_file" "$fd")"
        if [[ "$_verdict" == "other" ]]; then
            local _current_desc
            if [[ -e "$lock_file" ]]; then
                _current_desc="$(pgai_lock_describe_provenance "$(pgai_lock_read_provenance "$lock_file")")"
            else
                _current_desc="(no lock file at the path)"
            fi
            # CONVERT: quality-class narration with zero runtime-failure
            # risk -- nothing is refused. This process's own fd is closed
            # and its bookkeeping dropped exactly as pgai_lock_abandon does
            # after a reverify loss, and the file now at the path is left
            # to whoever holds it; the narration exists so a chain's exit
            # never silently unlinks another holder's lock.
            # CENSUS-ANCHOR: lock-provenance-release-not-owner-01
            pgai_emit_error_contract \
                "lock.provenance.release_not_owner" \
                "pgai_lock_release: ${lock_file} is no longer this process's lock (pid ${_PGAI_LOCK_HELD_PIDS[$lock_file]:-<unknown>} acquired it; the path now holds ${_current_desc}) -- closing this process's own fd and leaving the file to its current holder instead of removing it." \
                "No action needed -- the lock was reaped and reacquired by another process while this one was still running, and this release correctly declined to disturb the new holder. Inspect current lock ownership with: team/scripts/release-locks.sh"
            pgai_lock_abandon "$lock_file" "$fd"
            return 0
        fi

        rm -f -- "$lock_file" 2>/dev/null || true   # the one on-disk step
    fi

    # See the redirection-scope note in pgai_lock_acquire above: the group
    # wrapper keeps this 2>/dev/null from permanently silencing the calling
    # shell's stderr.
    { exec {fd}>&-; } 2>/dev/null || true

    if [[ -n "$lock_file" ]]; then
        unset "_PGAI_LOCK_HELD_FDS[$lock_file]" 2>/dev/null || true
        unset "_PGAI_LOCK_HELD_PIDS[$lock_file]" 2>/dev/null || true
    fi
    return 0
}

# ---------------------------------------------------------------------------
# pgai_lock_release_all_held
#
# Release every lock this process still holds per _PGAI_LOCK_HELD_FDS (see
# that array's own comment above) -- the trap/finally sweep that backs the
# guarantee described in this file's header: no exit path may leave a lock
# held. Idempotent and silent when nothing is held (the common case -- every
# well-behaved call site already released its own lock explicitly before
# this ever fires), so firing it more than once per exit (a signal handler
# that itself calls `exit`, re-triggering the EXIT trap -- see the
# installation block below) is harmless. Public so a caller with its own
# exit machinery can invoke it directly instead of relying on the
# trap/hook wiring this file installs automatically.
# ---------------------------------------------------------------------------
pgai_lock_release_all_held() {
    local _lock_file _fd
    for _lock_file in "${!_PGAI_LOCK_HELD_FDS[@]}"; do
        _fd="${_PGAI_LOCK_HELD_FDS[$_lock_file]}"
        echo "lock_provenance.sh: pgai_lock_release_all_held: ${_lock_file} (fd ${_fd}) was still held when this process began exiting -- releasing it now via the trap/finally sweep instead of leaving it orphaned (ownership-checked: a path another process has since taken over is abandoned, not touched)." >&2
        pgai_lock_release "$_lock_file" "$_fd"
    done
}

# ---------------------------------------------------------------------------
# pgai_lock_still_held <lock_file> <expected_pid>
#
# Cheap, read-only predicate: true (exit 0) when <lock_file>'s current
# on-disk provenance record names <expected_pid> in its pid field; false
# (exit 1) when the file is missing or unreadable, its provenance carries
# no pid field, or the recorded pid names a DIFFERENT process -- someone
# else now holds this lock.
#
# Deliberately never takes flock and never sleeps -- see this file's own
# "Chain-level ownership reverification" header section above for why: a
# per-mutating-phase-boundary check that itself contended for the lock or
# paused would be far too expensive to run unconditionally on every
# boundary of every chain. This is necessarily a point-in-time read: the
# lock could be reaped and reacquired by another process the instant
# after this returns 0. What repeated calls at every mutating phase
# boundary close is the WINDOW, not the instant -- a caller can never get
# more than one phase past having actually lost the lock, because the
# very next check reads whatever the new holder has by then written.
# ---------------------------------------------------------------------------
pgai_lock_still_held() {
    local lock_file="$1" expected_pid="$2"

    [[ -n "$lock_file" && -n "$expected_pid" ]] || return 1

    local content recorded_pid
    content="$(pgai_lock_read_provenance "$lock_file")"
    recorded_pid="$(_pgai_lock_field "$content" pid)"

    [[ -n "$recorded_pid" && "$recorded_pid" == "$expected_pid" ]]
}

# ---------------------------------------------------------------------------
# pgai_lock_reverify_or_narrate <lock_file> <expected_pid> <phase> <op> <task>
#
# Chain-level re-verification wrapper around pgai_lock_still_held, for a
# caller sitting at a mutating phase boundary (agent dispatch, the
# post-agent merge check, a queue-marker write -- see this file's own
# "Chain-level ownership reverification" header section above for the
# three call sites) who must not perform that phase unless it still
# actually holds <lock_file>.
#
# On success (still held): silent, returns 0. No stdout/stderr and no
# refusal-ledger write -- the healthy path carries zero extra output,
# exactly like the stale-threshold public wrappers' own read-only
# posture above (pgai_lock_stale_age_threshold_seconds/_derivation).
#
# On loss: narrates dual-channel (pgai_emit_error_contract) naming <phase>
# and the CURRENT holder's four provenance fields -- read fresh, at the
# moment of loss, so the narration always names whoever reaped and
# reacquired the lock, never a cached value -- then returns 1. This
# function's own contract stops at the narration; what the CALLER does
# next (skip the phase without performing it, and never mutate task
# state or queue files belonging to the chain that now legitimately owns
# the lock) is process_one_task's and wd_finalize_task's own contract
# (team/scripts/lib/wake_driver.sh, team/scripts/lib/wake_driver_finalize.sh).
#
# CENSUS CLASS: BLOCK-WORTHY -- same doctrine family as this file's own
# lock-provenance-01 / lock-provenance-contended-01 refusals above: a
# chain that no longer holds the project lock proceeding as though it
# still did is exactly the state-corrupting double-writer failure this
# fix exists to prevent, so this is an invariant-by-design stop, not a
# quality finding.
# CENSUS-ANCHOR: lock-provenance-reverify-01
# ---------------------------------------------------------------------------
pgai_lock_reverify_or_narrate() {
    local lock_file="$1" expected_pid="$2" phase="$3"
    local op="${4:-<unknown>}" task="${5:-<unknown>}"

    if pgai_lock_still_held "$lock_file" "$expected_pid"; then
        return 0
    fi

    local _current_provenance _current_desc
    _current_provenance="$(pgai_lock_read_provenance "$lock_file")"
    _current_desc="$(pgai_lock_describe_provenance "$_current_provenance")"

    pgai_emit_error_contract \
        "lock.provenance.reverify_lost" \
        "pgai_lock_reverify_or_narrate: pid ${expected_pid} (op=${op}, task=${task}) no longer holds ${lock_file} at the '${phase}' phase boundary -- it is now held by ${_current_desc}. Stopping this chain cleanly before performing the ${phase} phase rather than racing the new holder." \
        "No action needed -- the chain stopped itself before mutating shared state rather than race the new holder. Inspect current lock ownership with: team/scripts/release-locks.sh"
    return 1
}

# ---------------------------------------------------------------------------
# pgai_lock_abandon <lock_file> <fd>
#
# Let go of a lock this process no longer legitimately holds (per a
# pgai_lock_reverify_or_narrate loss above), WITHOUT touching the on-disk
# lock file -- the mirror-image case pgai_lock_release does not cover.
# pgai_lock_release's whole contract is clearing and removing a lock this
# process still actually owns; calling it after a reverify has already
# reported loss would truncate or remove whatever the NEW legitimate
# holder has since written there -- a second writer racing the first,
# exactly the failure this fix exists to prevent. pgai_lock_abandon only
# closes <fd> (a plain fd close on this process's own descriptor table;
# it neither contends with nor affects anyone else's flock on the
# current inode) and drops this process's own _PGAI_LOCK_HELD_FDS
# bookkeeping entry, so the trap/finally sweep (pgai_lock_release_all_held)
# never later calls pgai_lock_release on this path and corrupts the new
# holder's record on the way out.
#
# Safe to call with an empty/unset <fd> (no-op, returns 0), mirroring
# pgai_lock_release's own contract.
# ---------------------------------------------------------------------------
pgai_lock_abandon() {
    local lock_file="$1" fd="$2"

    if [[ -z "$fd" ]]; then
        return 0
    fi

    { exec {fd}>&-; } 2>/dev/null || true

    if [[ -n "$lock_file" ]]; then
        unset "_PGAI_LOCK_HELD_FDS[$lock_file]" 2>/dev/null || true
        unset "_PGAI_LOCK_HELD_PIDS[$lock_file]" 2>/dev/null || true
    fi
    return 0
}

# ---------------------------------------------------------------------------
# pgai_lock_refresh_held_mtime <lock_file> <fd>
#
# Freshen the mtime of a lock this process holds, so its age keeps
# measuring "time since the holder last showed signs of life" rather than
# "time since acquisition" -- see "Held-lock refresh" in this file's
# header. A plain `touch` of the path: no flock is taken, re-taken, or
# released (flock is advisory over ACQUISITION, not over ordinary I/O),
# and the provenance record is not rewritten. Ownership-checked exactly as
# pgai_lock_release is (_pgai_lock_ownership_verdict): the touch happens
# only when the file at <lock_file> is the very inode <fd> holds the flock
# on, so a chain that lost the lock can never make the new holder's file
# look fresher than it is. Silent, and returns 0 in every case -- an empty
# <fd> or <lock_file>, a path this process's own bookkeeping no longer
# lists, a path taken over by another holder, or a touch that fails --
# because it runs inside a poll loop under errexit, where a refresh must
# never be able to end the task it exists to protect.
# ---------------------------------------------------------------------------
pgai_lock_refresh_held_mtime() {
    local lock_file="$1" fd="$2"
    [[ -n "$lock_file" && -n "$fd" ]] || return 0
    [[ -n "${_PGAI_LOCK_HELD_FDS[$lock_file]+set}" ]] || return 0
    [[ "$(_pgai_lock_ownership_verdict "$lock_file" "$fd")" == "own" ]] || return 0
    touch -c -- "$lock_file" 2>/dev/null || true
    return 0
}

# ---------------------------------------------------------------------------
# pgai_lock_set_task <lock_file> <fd> <task>
#
# Rewrite the task= field of a lock this process holds, keeping holder,
# pid, op and acquired_at exactly as recorded. A wake chain takes its repo
# lock before it has chosen a task (task=none) and calls this as it
# dispatches each one, so the lock names what its holder is doing.
#
# Why in place, and why it is still never a partial record: the flock lives
# on the lock file's inode, so the staged-file-and-rename the acquire path
# uses would move the path onto an inode nobody has locked. Instead the new
# record is written into the held inode itself -- reached through
# /proc/self/fd/<fd>, never the path, so a lock this process has lost is
# never written to -- by ONE write(2) at offset 0 (dd with a block size
# larger than the record and iflag=fullblock), with no truncate: a record
# shorter than the one it replaces is padded with trailing newlines, which
# every reader skips. The file is therefore never empty and never shorter
# than a complete record at any instant a reader can observe.
#
# Ownership-checked like pgai_lock_refresh_held_mtime: does nothing unless
# this process's bookkeeping lists <lock_file> and the file at the path is
# the very inode <fd> holds. Returns 0 when the record was rewritten, 1 when
# it was not (blank or multi-line arguments, a lock this process does not
# hold, no /proc, or a failed write) -- the lock itself is unaffected
# either way, so a caller treats 1 as a narration, never a stop.
# ---------------------------------------------------------------------------
pgai_lock_set_task() {
    local lock_file="$1" fd="$2" task="$3"
    local LC_ALL=C
    [[ -n "$lock_file" && -n "$fd" && -n "$task" ]] || return 1
    [[ "$task" != *$'\n'* ]] || return 1
    [[ -n "${_PGAI_LOCK_HELD_FDS[$lock_file]+set}" ]] || return 1
    [[ "$(_pgai_lock_path_verdict "$lock_file" "$fd")" == "own" ]] || return 1

    local _held="/proc/self/fd/${fd}" _old _old_size _acquired_at _new
    _old="$(cat "$_held" 2>/dev/null)" || return 1
    _old_size="$(stat -L -c '%s' "$_held" 2>/dev/null)" || return 1
    [[ "$_old_size" =~ ^[0-9]+$ ]] || return 1

    _new="$(printf 'holder=%s\npid=%s\nop=%s\ntask=%s' \
        "$(_pgai_lock_field "$_old" holder)" "$(_pgai_lock_field "$_old" pid)" \
        "$(_pgai_lock_field "$_old" op)" "$task")"
    _acquired_at="$(_pgai_lock_field "$_old" acquired_at)"
    [[ -n "$_acquired_at" ]] && _new+=$'\n'"acquired_at=${_acquired_at}"
    _new+=$'\n'
    while (( ${#_new} < _old_size )); do
        _new+=$'\n'
    done

    printf '%s' "$_new" | dd of="$_held" bs=65536 iflag=fullblock conv=notrunc status=none 2>/dev/null || return 1
    return 0
}

# ---------------------------------------------------------------------------
# Install the release-all-held sweep on this process's own exit, exactly
# once no matter how many times this file is sourced (project_paths.sh's
# "safe to source multiple times" convention applies to the rest of this
# file's function redefinitions; trap/hook installation is a one-time side
# effect, guarded here so re-sourcing never double-chains it).
#
# Two integration modes, chosen by what is already installed for EXIT:
#
#   - An EXIT trap is already active. True for the whole wake/OVERWATCH
#     chain: wake_bootstrap.sh installs script_common.sh's cleanup_on_exit
#     on EXIT/SIGTERM/SIGINT/SIGHUP before any lib that sources this file
#     gets a chance to. cleanup_on_exit stops CLEANUP_ON_EXIT_PROCESS_GROUP
#     (TERM, grace, KILL) before it calls the pre-hook, so the release below
#     always runs after the provider's process group is empty. Composing via CLEANUP_ON_EXIT_PRE_HOOK is not optional
#     here -- cleanup_on_exit's own last line is `exit $exit_code`, so
#     anything appended AFTER it in a bare trap string would never run.
#     The hook variable is consulted BEFORE that exit call (see
#     script_common.sh), which is the only correct place to add work, and
#     chaining preserves whatever hook a caller already registered rather
#     than displacing it.
#   - No EXIT trap is active yet -- a standalone caller (release-locks.sh,
#     a unit test sourcing this file directly). This file installs its own
#     bare EXIT/SIGTERM/SIGINT trap so the guarantee holds outside the
#     wake chain too, without depending on any other file's cooperation --
#     "the fix belongs in one place: the lock library's own trap/finally."
# ---------------------------------------------------------------------------
if [[ -z "${_PGAI_LOCK_PROVENANCE_EXIT_HOOK_INSTALLED:-}" ]]; then
    _PGAI_LOCK_PROVENANCE_EXIT_HOOK_INSTALLED=1

    if [[ -n "$(trap -p EXIT)" ]]; then
        _PGAI_LOCK_PROVENANCE_PRIOR_HOOK="${CLEANUP_ON_EXIT_PRE_HOOK:-}"
        _pgai_lock_provenance_exit_hook() {
            local _exit_code="$1"
            if [[ -n "${_PGAI_LOCK_PROVENANCE_PRIOR_HOOK}" ]]; then
                "${_PGAI_LOCK_PROVENANCE_PRIOR_HOOK}" "$_exit_code" || true
            fi
            pgai_lock_release_all_held
        }
        CLEANUP_ON_EXIT_PRE_HOOK=_pgai_lock_provenance_exit_hook
    else
        trap 'pgai_lock_release_all_held' EXIT
        trap 'pgai_lock_release_all_held; exit 143' SIGTERM
        trap 'pgai_lock_release_all_held; exit 130' SIGINT
    fi
fi
