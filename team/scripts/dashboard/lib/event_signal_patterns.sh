#!/usr/bin/env bash
# event_signal_patterns.sh — single-source classification of a wake-log line
# as SIGNAL (worth an operator's glance) or BOILERPLATE (routine idle-cycle
# noise to drop).
#
# WHY THIS EXISTS
# ----------------
# A raw wake-log stream is mostly idle-cycle chatter: re-checking WAITING or
# BLOCKED tasks that turn up nothing, "no BACKLOG tasks" idle exits, sleep
# and stagger delays, and per-tick timing lines. Any renderer that wants to
# show only the events an operator actually cares about — a task dispatched,
# a task finishing with its state, real completed work, a HALT, a queue
# promotion, a reaped worktree, a dual-channel error/message/remedy block, or
# a sweep-census skip and its reason — needs one shared answer to "is this
# line signal or noise", so every consumer agrees and nobody carries its own
# copy of the pattern list or an inline grep. This file is that one answer.
# The raw, unfiltered logs are never lost: they stay available in the logs
# window and via cron-tail.sh for forensics — this library only decides what
# a signal-only view surfaces.
#
# TO ADD A NEW SIGNAL PATTERN: append one extended-regex string to the
# EVENT_SIGNAL_PATTERNS array below and nothing else. Both
# dashboard_event_signal_is_signal and dashboard_event_signal_filter read
# that same array, so the new pattern takes effect everywhere this library
# is used the moment the line is added. A pattern added this way declares
# no collapse metadata, which is correct: it means "render every
# occurrence", the only behaviour that existed before collapse metadata
# did, and it stays true for every class that has no reason to repeat.
#
# THE COLLAPSE CONTRACT: a class that legitimately repeats byte-for-byte
# except for its timestamp and agent name (sweep-census skips are the
# first example) can declare a collapse identity beside its own pattern in
# EVENT_SIGNAL_COLLAPSE_KEYS, an associative array keyed by the exact
# pattern string it belongs to. The value is itself an extended-regex with
# one capture group: whatever the group captures IS the identity, so a
# renderer folding consecutive same-identity lines together stays exactly
# in sync with what this library considers "the same event" — no
# per-class special case leaks into the renderer, which only ever calls
# the accessor below. Declaring collapse metadata for a pattern is a
# second, explicit line beside the one that added the pattern; a pattern
# that declares none is untouched, so adding an ordinary non-collapsing
# pattern is still the single-line edit described above. Absence from
# EVENT_SIGNAL_COLLAPSE_KEYS means "never collapse" — there is no default
# that folds a class its author did not opt in.
#
# USAGE
# -----
#   source ".../dashboard/lib/event_signal_patterns.sh"
#
#   # Classify one already-read line:
#   if dashboard_event_signal_is_signal "$line"; then ...; fi
#
#   # Filter a stream, keeping only signal lines (order preserved):
#   dashboard_event_signal_filter < raw.log > signal-only.log
#   tail -F some.log | dashboard_event_signal_filter
#
#   # Pre-filter a large stream at grep(1) speed before a per-line
#   # bash loop touches it (the authoritative check stays
#   # dashboard_event_signal_is_signal; this is a fast candidate pass):
#   grep -E "$(dashboard_event_signal_ere)" raw.log
#
#   # Get a line's collapse identity, when its matching pattern declares
#   # one (empty output and a non-zero return otherwise):
#   if key="$(dashboard_event_signal_collapse_key "$line")"; then
#     ...
#   fi
#
#   # The same lookup with no subshell fork, for a per-line loop that
#   # calls it often enough for fork cost to matter:
#   if dashboard_event_signal_collapse_key_var "$line" key; then
#     ...
#   fi
#
# This file only defines two arrays and five functions when sourced: no
# output, no command execution, no side effects beyond those definitions,
# so it is safe to source from a `set -euo pipefail` caller.

# Guard against double-sourcing re-declaring the array (harmless, but the
# guard keeps repeated `source` calls from being interviewed for a bug they
# do not have).
if [[ -z "${PGAI_EVENT_SIGNAL_PATTERNS_LOADED:-}" ]]; then
PGAI_EVENT_SIGNAL_PATTERNS_LOADED=1

# EVENT_SIGNAL_PATTERNS — ordered list of bash extended-regex fragments
# (as consumed by `[[ "$line" =~ $pattern ]]`). A line is SIGNAL when it
# matches ANY entry; order does not affect WHICH lines match, only how
# many comparisons a per-line classifier spends finding out. It is
# ordered by measured real-world match frequency, highest first: a live
# root's daily signal volume is dominated (>96%, see event-band.sh's own
# fixture sizing) by sweep-census skip lines, so putting that pattern
# last made dashboard_event_signal_is_signal walk all thirteen
# comparisons on nearly every surviving line before finding its match --
# a real per-line cost once thousands of today's signal lines survive
# the read-bound prefilter. Keep the highest-traffic pattern(s) first; a
# class this file's own header calls "routine idle-cycle noise" gains
# nothing from moving up.
EVENT_SIGNAL_PATTERNS=(
  # Sweep-census skip lines, including their stated reason -- the
  # majority of a live root's daily signal volume, so checked first.
  'sweep-census:.*skipped'

  # Task-finished lines that carry the task's own terminal state.
  'finished task [^[:space:]]+ with state [A-Za-z_-]+'

  # Agent dispatch: a task is handed to an agent and starts running.
  'transitioned to WORKING \[A\]'

  # Done-lines reporting a non-zero completed-task count for the wake.
  'done: completed=[1-9][0-9]*'

  # HALT detection, whichever checkpoint logged it.
  'HALT[-_A-Za-z]* flag detected'
  'per-project HALT present'

  # Non-zero WAITING-to-BACKLOG promotion counts, and the per-task line
  # logged when one task's own prerequisites clear.
  'promoted [1-9][0-9]* task\(s\)'
  'prerequisites satisfied, promoting to BACKLOG'

  # Reaps of a worktree/branch abandoned by an interrupted task.
  'tearing down prior worktree'
  'stale-active reconciliation: reset [1-9][0-9]* orphaned'

  # Dual-channel error blocks: the error/message/remedy triple.
  '^error:[[:space:]]'
  '^message:[[:space:]]'
  '^remedy:[[:space:]]'
)

# EVENT_SIGNAL_COLLAPSE_KEYS — optional per-pattern collapse metadata,
# keyed by the exact pattern string it belongs to in EVENT_SIGNAL_PATTERNS.
# A pattern with no entry here declares no collapse metadata and never
# collapses (see the header's "COLLAPSE CONTRACT"). Each value is an
# extended-regex with exactly one capture group; dashboard_event_signal_
# collapse_key applies it to the matching line and returns the capture as
# the identity, so whatever the group leaves OUT of its capture (a leading
# timestamp, an agent name) is what the renderer is free to fold away.
declare -A EVENT_SIGNAL_COLLAPSE_KEYS=(
  # Sweep-census skips repeat once per agent per wake for as long as a
  # bundle sits in the same state, byte-identical apart from the
  # timestamp and which agent logged it. Capturing from "sweep-census:"
  # to end of line strips exactly that prefix, so the identity still
  # differs when the named bundle or the stated reason differs.
  ['sweep-census:.*skipped']='(sweep-census:.*)$'
)

fi

# ---------------------------------------------------------------------------
# dashboard_event_signal_is_signal <line>
#
# Returns 0 (true) when <line> matches any pattern in EVENT_SIGNAL_PATTERNS,
# 1 (false) otherwise. Never prints anything.
# ---------------------------------------------------------------------------
dashboard_event_signal_is_signal() {
  local _desp_line="$1"
  local _desp_pattern
  for _desp_pattern in "${EVENT_SIGNAL_PATTERNS[@]}"; do
    if [[ "$_desp_line" =~ $_desp_pattern ]]; then
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# dashboard_event_signal_filter
#
# Reads lines from stdin, writes to stdout only the lines classified as
# SIGNAL by dashboard_event_signal_is_signal, in their original order.
# Boilerplate lines are dropped silently — the raw stream this was read
# from remains the forensic record, so nothing is lost, only hidden from
# this particular view.
# ---------------------------------------------------------------------------
dashboard_event_signal_filter() {
  local _desf_line
  while IFS= read -r _desf_line || [[ -n "$_desf_line" ]]; do
    if dashboard_event_signal_is_signal "$_desf_line"; then
      printf '%s\n' "$_desf_line"
    fi
  done
}

# ---------------------------------------------------------------------------
# dashboard_event_signal_ere
#
# Prints EVENT_SIGNAL_PATTERNS joined into one extended-regex alternation,
# parenthesized as a single group (e.g. "(pat1|pat2|pat3)"), suitable for
# `grep -E`. Every pattern here is already a POSIX extended-regex fragment
# consumed elsewhere via bash's own `[[ =~ ]]` (the same regex dialect
# `grep -E` speaks), so the joined form is a faithful pre-filter, not an
# approximation.
#
# This is the SAME array dashboard_event_signal_is_signal reads — never a
# second pattern list — so a caller piping a stream through
# `grep -E "$(dashboard_event_signal_ere)"` ahead of the authoritative
# per-line check gets a candidate-narrowing pass that stays exactly in
# sync with the library: adding a pattern to EVENT_SIGNAL_PATTERNS changes
# both the classifier and this pre-filter in the same one-line edit.
# ---------------------------------------------------------------------------
dashboard_event_signal_ere() {
  local IFS='|'
  printf '(%s)' "${EVENT_SIGNAL_PATTERNS[*]}"
}

# ---------------------------------------------------------------------------
# dashboard_event_signal_collapse_key <line>
#
# Prints <line>'s collapse identity and returns 0 when <line> matches a
# pattern in EVENT_SIGNAL_PATTERNS that declares an entry in
# EVENT_SIGNAL_COLLAPSE_KEYS. Prints nothing and returns 1 when <line>
# matches no pattern at all, or matches only patterns that declare none —
# absence of collapse metadata means "render every occurrence", the
# classifier's existing behaviour for every class other than the ones that
# opt in.
#
# The identity is whatever the declared capture-group regex captures from
# <line>: the varying parts a renderer should ignore (a timestamp, an
# agent name) sit outside that group by construction, so two occurrences
# of the same underlying event collapse to the same identity regardless of
# when or which agent logged them, while a change to anything the group
# DOES capture (a bundle path, a stated reason) produces a different
# identity and is never folded away.
# ---------------------------------------------------------------------------
dashboard_event_signal_collapse_key() {
  local _desk_line="$1"
  local _desk_result
  if dashboard_event_signal_collapse_key_var "$_desk_line" _desk_result; then
    printf '%s\n' "$_desk_result"
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# dashboard_event_signal_collapse_key_var <line> <outvar>
#
# Same match and identity as dashboard_event_signal_collapse_key -- this
# function does the one pattern walk both entry points share -- but writes
# the result into the CALLER's <outvar> (via `printf -v`) instead of
# printing it to stdout. A caller in a per-line loop over thousands of
# candidate lines wants this form: consuming a printed result requires
# `$(...)` command substitution, and bash forks a subshell for every one
# of those, one fork per call whether or not the loop above it already
# caches repeat calls away -- a real cost in its own right once a loop
# calls this dozens or hundreds of times. This form pays no fork at all.
# Returns 0/1 exactly as dashboard_event_signal_collapse_key does; <outvar>
# is set to the identity on a match and to the empty string otherwise.
# ---------------------------------------------------------------------------
dashboard_event_signal_collapse_key_var() {
  local _desk_line="$1" _desk_outvar="$2"
  local _desk_pattern _desk_collapse
  for _desk_pattern in "${EVENT_SIGNAL_PATTERNS[@]}"; do
    if [[ "$_desk_line" =~ $_desk_pattern ]]; then
      _desk_collapse="${EVENT_SIGNAL_COLLAPSE_KEYS[$_desk_pattern]:-}"
      if [[ -n "$_desk_collapse" ]] && [[ "$_desk_line" =~ $_desk_collapse ]]; then
        printf -v "$_desk_outvar" '%s' "${BASH_REMATCH[1]}"
        return 0
      fi
    fi
  done
  printf -v "$_desk_outvar" '%s' ""
  return 1
}
