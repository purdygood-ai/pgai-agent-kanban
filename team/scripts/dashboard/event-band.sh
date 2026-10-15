#!/usr/bin/env bash
# event-band.sh
# Merged, chronologic, agent-tagged, signal-only event stream for the
# schedule window's event band.
#
# Replaces reading five separate raw cron tails to find the handful of
# lines that actually matter: this renderer reads every agent's
# cron-<agent>.log, classifies each line as SIGNAL or BOILERPLATE via the
# single shared classifier (lib/event_signal_patterns.sh — this script
# carries no pattern list of its own), keeps only today's SIGNAL lines,
# and prints them as one interleaved stream in ascending timestamp order
# regardless of which agent's log a line came from.
#
# The raw, unfiltered per-agent logs are never touched or hidden by this
# script — they remain available exactly as before via the logs window
# and cron-tail.sh for forensics. This is a second, filtered view, not a
# replacement for the raw one.
#
# Output shape (one line per SIGNAL event, oldest first):
#   [<agent> HH:MM:SS] <line text after its own leading timestamp>
#
# A line whose matching pattern declares a collapse identity in
# lib/event_signal_patterns.sh (EVENT_SIGNAL_COLLAPSE_KEYS) folds every
# occurrence sharing that identity across the WHOLE DAY's merged stream —
# every agent, not just consecutive lines — into a single row at its
# first occurrence's position, carrying the total occurrence count and
# that first-seen time:
#   [<agent> HH:MM:SS] <line text> (x<count>, first seen HH:MM:SS)
# A line with no collapse identity always renders exactly once, exactly
# as it does today.
#
# The bare "error: <key>" line for lock_provenance.sh's two dual-channel
# lock narrations (lock.provenance.contended,
# lock.stale_threshold.floor_enforced) is a special case of the above:
# recognized by exact key text, it folds not just with its own repeats
# but with its own immediately-following message:/remedy: lines into ONE
# row per occurrence, keyed by the narration name, and additionally
# carries the distinct set of agents that hit it:
#   [<agent> HH:MM:SS] <key> (x<count>, first seen HH:MM:SS, agents: <a,b,c>)
# Every OTHER dual-channel block (a HALT reason, a gate refusal, ...)
# renders its error:/message:/remedy: lines separately, exactly as
# before -- this fold is scoped to those two literal keys only.
#
# When no agent log carries a SIGNAL line from today:
#   no wake events yet today
#
# Usage:
#   event-band.sh [--kanban-root <path>] [--no-color]
#
# Options:
#   --kanban-root <path>   Override the kanban root (default:
#                          PGAI_AGENT_KANBAN_ROOT_PATH or ~/pgai_agent_kanban)
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
# Log path pattern read per agent:
#   $KANBAN_ROOT/logs/cron-<agent>.log
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root directory
#   NO_COLOR                     — set to any non-empty value to disable color
#   TERM=dumb                    — also disables color
#   PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES
#     — override the per-agent tail-read bound (default:
#       kanban.cfg [dashboard] event_band_read_bound_lines, or 20000)
#
# Runs standalone outside tmux: prints the current band once to stdout
# and exits 0 (no polling, no tail -F). A hosting pane re-renders it by
# re-invoking this script on its own refresh cadence, the same pattern
# every other renderer in this directory uses.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table — see team/scripts/lib/script_common.sh).
# shellcheck source=../lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# Source temp-file helpers so the merge scratch file lives under the
# framework temp root, never bare /tmp.
# shellcheck source=../lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# Source the single shared signal/boilerplate classifier. Line
# classification is delegated entirely to this library — this script
# defines no pattern list, no grep list, and no regex array of its own.
# shellcheck source=lib/event_signal_patterns.sh
source "${SCRIPT_DIR}/lib/event_signal_patterns.sh"

# Source read_ini so the per-agent read bound (below) is an operator
# config value rather than a hardcoded constant -- safe to source
# unconditionally (function definitions only, no top-level side effects).
# shellcheck source=../lib/ini_parser.sh
source "${SCRIPT_DIR}/../lib/ini_parser.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""
NO_COLOR_ARG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_OVERRIDE="${2:-}"
      shift 2
      ;;
    --no-color)
      NO_COLOR_ARG=true
      shift
      ;;
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      # Unknown arguments silently ignored for forward compatibility,
      # consistent with the sibling dashboard renderers.
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
  KANBAN_ROOT="$KANBAN_ROOT_OVERRIDE"
fi
LOG_DIR="${KANBAN_ROOT}/logs"

# ---------------------------------------------------------------------------
# Color support (honor NO_COLOR, --no-color, and TERM=dumb)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]] || [[ "$NO_COLOR_ARG" == "true" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
declare -A AGENT_COLOR
AGENT_COLOR[pm]="$(ansi_code cyan)"
AGENT_COLOR[coder]="$(ansi_code green)"
AGENT_COLOR[writer]="$(ansi_code yellow)"
AGENT_COLOR[tester]="$(ansi_code blue)"
AGENT_COLOR[cm]="$(ansi_code magenta)"

# The five agents whose cron tails the event band replaces — same roster
# as the schedule window's per-agent columns (cleanup and overwatch fire
# weekly and are not part of this window).
AGENT_ORDER=(pm coder writer tester cm)

# ---------------------------------------------------------------------------
# Merge every agent's log into one sortable-by-timestamp scratch file,
# keeping only SIGNAL lines from today. Each scratch row is:
#   <sort_key>\t<agent>\t<display_time>\t<line text after its own timestamp>
# ---------------------------------------------------------------------------
TODAY="$(date +%F)"
# A global counter, incremented once per row written to MERGE_FILE
# across every agent, zero-padded into the sort key below (see the
# printf near the bottom of the per-line loop). It is what makes the
# render a pure function of its input: appended after the row's
# date+time, it gives every row a distinct sort key even when two rows
# share the same HH:MM:SS, so `sort` has no tie to break arbitrarily and
# the merged stream comes out in the exact order the rows were written
# -- each agent in AGENT_ORDER, each agent's own lines in file order --
# every time, for identical input. Ten digits comfortably covers any
# READ_BOUND × agent-count product an operator could configure.
MERGE_SEQ=0
MERGE_FILE="$(pgai_mktemp event-band)"
# One reusable scratch file the per-agent prefilter writes into and the
# per-line loop below reads back from -- see the SIGNAL_FILE note ahead
# of that loop for why a real file replaces the process-substitution
# pipe it used to read from directly.
SIGNAL_FILE="$(pgai_mktemp event-band-signal)"
trap 'rm -f "$MERGE_FILE" "$SIGNAL_FILE"' EXIT

# READ_BOUND caps each agent's log to its last N lines via `tail -n`,
# which (GNU coreutils) seeks backward from the end of a regular file
# rather than reading it start to finish -- the cost stays O(READ_BOUND),
# never O(file size), so an append-only, never-rotated cron log growing
# for years costs this renderer nothing extra (the pattern status-band.sh
# already uses via its own `tail -n 5000`).
#
# Resolved in the same three-tier order the sibling snapshot cache's TTL
# uses (team/scripts/dashboard/lib/snapshot.sh
# dashboard_snapshot_ttl_seconds): an env override first, for
# tests/operators; kanban.cfg [dashboard] event_band_read_bound_lines
# second; a hardcoded default last, so a fresh install with no config at
# all still renders correctly.
#
# Default sized against production volume, not fixture volume: the five
# live cron-<agent>.log files append roughly 28,000 lines/day between
# them, an average of ~5,600 lines/day per agent. 20,000 gives roughly
# 3.5x headroom over that per-agent average -- enough for one agent's
# day to run far busier than the fleet norm -- while staying small
# enough that even a pathological all-signal tail (every surviving line
# reaching the per-line bash classifier below, never realistic in
# practice since real wake cycles are mostly idle-cycle boilerplate, but
# worth bounding against) still clears comfortably inside a few seconds.
# A 20,000-line tail is a read of well under 1 MB against logs already
# measured in the hundreds of MB, so the bound adds negligible time
# regardless of how large the historical file grows.
READ_BOUND="${PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES:-}"
if [[ -z "$READ_BOUND" ]]; then
  if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    READ_BOUND="$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard event_band_read_bound_lines 20000 2>/dev/null || echo 20000)"
  else
    READ_BOUND=20000
  fi
fi
if ! [[ "$READ_BOUND" =~ ^[0-9]+$ ]] || (( READ_BOUND <= 0 )); then
  READ_BOUND=20000
fi

# One extended-regex alternation built from the shared pattern array
# (lib/event_signal_patterns.sh) -- never a pattern list of this script's
# own. Piping each agent's bounded tail through `grep -E` this way lets a
# fast C-level scan discard boilerplate candidates before they ever reach
# the per-line bash loop below.
#
# This ERE is the join of the exact same POSIX-extended-regex fragments
# dashboard_event_signal_is_signal iterates one at a time, so a line
# surviving this grep has already matched at least one of them -- the
# classifier's per-line answer is provably identical to grep's, never a
# mere approximation of it (both speak the same regex dialect; none of
# the fragments carries a top-level `|` of its own for the join to
# mis-group, and the three `^`-anchored fragments anchor per-alternative
# under POSIX ERE, exactly as they do standing alone). A per-line
# re-classification of a line this grep already accepted can therefore
# only ever confirm a foregone conclusion, and measurement against
# production-diverse input found that foregone-conclusion recheck (the
# thirteen-pattern walk on every line the single-pattern fast path
# missed, which is most of a production root's diverse traffic) to be
# the dominant per-line cost once thousands of today's SIGNAL lines
# survive the read-bound prefilter. Trusting
# this prefilter as authoritative -- rather than re-asking the same
# question inside the loop -- is the fix: it removes the cost instead of
# shaving it.
EVENT_SIGNAL_ERE="$(dashboard_event_signal_ere)"

# Placeholder written to the scratch file's collapse-key column for a
# line with no collapse identity (see the sentinel note below). A
# capture from EVENT_SIGNAL_COLLAPSE_KEYS always starts with its
# pattern's own literal text (e.g. "sweep-census:"), so a bare hyphen
# never collides with a real identity.
NO_COLLAPSE_KEY="-"

# Memoizes dashboard_event_signal_collapse_key's result per distinct
# post-timestamp line text (declared once, reused by every agent's
# loop below). Necessary, not cosmetic: consuming the accessor's
# printed identity requires command substitution ($(...)), and bash
# forks a subshell for every command substitution -- calling it fresh
# for each of a repeated class's whole-day occurrences (the exact
# volume this feature exists to fold) would pay one fork per
# occurrence merely to discover they all share one identity. A repeated
# line's text past its own timestamp is identical on every occurrence
# by definition (that sameness is what makes it foldable), so caching
# on that text turns a fork-per-occurrence cost into a fork-per-
# DISTINCT-identity cost -- the accessor itself is still the only place
# that ever computes an identity, this only avoids asking it the same
# question twice.
declare -A COLLAPSE_KEY_CACHE=()

# Open the merge scratch file once and hold the descriptor for every
# line this loop writes, rather than letting `printf … >> "$MERGE_FILE"`
# reopen (open+seek-to-end+close) the file on every single surviving
# line -- a real cost once thousands of today's SIGNAL lines survive
# the read-bound prefilter. One `exec` here still yields the identical
# byte stream in the identical order; only when the file is opened
# changes, never what is written to it.
exec {MERGE_FD}>>"$MERGE_FILE"

for agent in "${AGENT_ORDER[@]}"; do
  log_file="${LOG_DIR}/cron-${agent}.log"
  [[ -f "$log_file" ]] || continue

  # The most recently parsed leading timestamp in THIS agent's file,
  # reset per agent since a bare continuation line only ever belongs to
  # the timestamped parent line immediately above it in the same log
  # file -- carrying a value across agents would attribute a
  # continuation line to an unrelated agent's timestamp. Empty until
  # this agent's first timestamped line is read.
  last_date_part=""
  last_time_part=""

  # Materialize this agent's prefiltered candidates into SIGNAL_FILE, a
  # real seekable file, rather than piping them straight into the while
  # loop via process substitution: bash's `read` builtin has no way to
  # know a delimiter is coming from an unseekable descriptor without
  # consuming past it, so on a pipe it falls back to a read(2) syscall
  # PER BYTE rather than the buffered block reads it uses on a regular
  # file -- roughly 20x slower per line against production-shaped
  # today volume, measured directly, and the dominant remaining cost
  # once the classifier and the merge-file writes were fixed. The
  # candidate set and its order are unchanged; only how the loop below
  # reads them is.
  #
  # grep exits 1 when a boilerplate-only tail has no signal candidates
  # at all -- expected, not a failure, so `|| true` keeps it from
  # tripping `set -e`.
  tail -n "$READ_BOUND" "$log_file" | grep -E "$EVENT_SIGNAL_ERE" > "$SIGNAL_FILE" || true

  # mapfile reads the whole (already read-bounded, already
  # signal-prefiltered) file in one block read rather than the
  # line-at-a-time `read` builtin -- a second, smaller instance of the
  # same read-pattern cost the SIGNAL_FILE change above addresses, worth
  # taking here since SIGNAL_FILE's line count is exactly the volume
  # the read bound limits.
  mapfile -t _agent_signal_lines < "$SIGNAL_FILE"

  _asl_count=${#_agent_signal_lines[@]}
  for (( _asl_idx=0; _asl_idx<_asl_count; _asl_idx++ )); do
    line="${_agent_signal_lines[_asl_idx]}"
    [[ -z "$line" ]] && continue
    # No re-classification here: every line in _agent_signal_lines
    # already survived the grep -E prefilter above against this exact
    # same pattern set, which is proof enough it is SIGNAL (see the
    # EVENT_SIGNAL_ERE comment). Re-testing it against the thirteen
    # patterns one more time per line measured as the dominant per-line
    # cost against production-diverse input; dropping the recheck
    # removes that cost rather than shaving it.

    # A wake-log line's own timestamp is a leading "[ISO8601] " prefix
    # (team/scripts/lib/wake_bootstrap.sh's log() function). Tolerate the
    # shape loosely: match the date+time and let everything after the
    # closing bracket (offset, "Z", trailing space) vary.
    if [[ "$line" =~ ^\[([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}:[0-9]{2}:[0-9]{2})[^]]*\][[:space:]]*(.*)$ ]]; then
      date_part="${BASH_REMATCH[1]}"
      time_part="${BASH_REMATCH[2]}"
      rest="${BASH_REMATCH[3]}"
      last_date_part="$date_part"
      last_time_part="$time_part"
    else
      # No parseable leading timestamp — e.g. a dual-channel
      # error:/message:/remedy: continuation line, which the wake log
      # emits bare, directly under its own timestamped parent line.
      # Inherit the parent's own date+time (the most recent timestamped
      # line this same agent's file yielded, tracked in
      # last_date_part/last_time_part above) so the row displays the
      # time the event actually happened rather than the time it was
      # rendered, and sorts adjacent to the parent it explains rather
      # than to whatever second the render started in.
      #
      # A continuation line with no parent yet -- one that is somehow
      # the very first candidate line in an agent's file -- has no
      # timestamp to inherit. Drop it rather than inventing one: every
      # real dual-channel continuation line is emitted directly under
      # its own timestamped parent (wake_bootstrap.sh's log()
      # function), so this case does not arise in practice, and
      # synthesizing "now" for it would reintroduce a displayed
      # timestamp absent from the input.
      if [[ -z "$last_date_part" ]]; then
        continue
      fi
      date_part="$last_date_part"
      time_part="$last_time_part"
      rest="$line"
    fi

    [[ "$date_part" == "$TODAY" ]] || continue

    # Narration-rate display fold: lock_provenance.sh's own
    # per-process memoization already caps how many TIMES the
    # lock.provenance.contended / lock.stale_threshold.floor_enforced
    # dual-channel blocks are written per process, but a long chain
    # still spans many separate wake-firing processes across many
    # agents, and each block is three physical lines
    # (error:/message:/remedy:) that would otherwise render as three
    # separate rows per occurrence. Recognizing the bare "error: <key>"
    # line for exactly these two known keys and folding its immediately
    # following message:/remedy: lines into it turns each occurrence
    # into ONE row, collapsed by key across the whole day exactly like
    # any other collapse identity below -- with the agent list shown
    # (show_agents=1) since "which agents hit this" is the point of the
    # count. Scoped to these two literal keys only: every OTHER
    # dual-channel block (HALT, gate refusals, ...) is untouched and
    # still renders all three lines exactly as before.
    #
    # Exact `case` string comparison, never `=~`: pgai_format_error_
    # contract's house shape ("error:   <key>", three literal spaces,
    # nothing else on the line) is fully machine-controlled -- this is
    # this script's own producer, not human-edited text -- so there is
    # no format variance to tolerate here, and a plain string compare
    # costs no regex-engine invocation on the vast majority of lines
    # that are not this shape at all (a per-line cost that matters at
    # this script's measured production volume; see the collapse-key
    # cache comments above for the sibling cost this mirrors).
    show_agents=0
    collapse_key=""
    case "$rest" in
      "error:   lock.provenance.contended")
        collapse_key="lock.provenance.contended"
        ;;
      "error:   lock.stale_threshold.floor_enforced")
        collapse_key="lock.stale_threshold.floor_enforced"
        ;;
    esac
    if [[ -n "$collapse_key" ]]; then
      rest="$collapse_key"
      show_agents=1
      # Consume the message: line immediately following, and (only when
      # that message: line was itself found) the remedy: line after
      # that -- the fixed order pgai_format_error_contract's house shape
      # always writes them in. A block that is not immediately followed
      # by its own message: line (a read-bound tail split mid-block, in
      # practice never seen from a single log() call) is left as this
      # bare key row alone; the file's raw lines are never lost, only
      # this filtered/folded view stops short of merging them. Glob
      # prefix match, not `=~`, for the same cost reason as above.
      if (( _asl_idx + 1 < _asl_count )) \
        && [[ "${_agent_signal_lines[_asl_idx + 1]}" == message:* ]]; then
        _asl_idx=$(( _asl_idx + 1 ))
        if (( _asl_idx + 1 < _asl_count )) \
          && [[ "${_agent_signal_lines[_asl_idx + 1]}" == remedy:* ]]; then
          _asl_idx=$(( _asl_idx + 1 ))
        fi
      fi
      printf '%s#%010d\t%s\t%s\t%s\t%s\t%s\n' "${date_part}T${time_part}" "$MERGE_SEQ" "$agent" "$time_part" "$collapse_key" "$show_agents" "$rest" >&"$MERGE_FD"
      MERGE_SEQ=$(( MERGE_SEQ + 1 ))
      continue
    fi

    # Ask the shared library for this line's collapse identity -- never
    # a pattern or regex of this script's own -- but only once per
    # distinct $rest text (see the cache declaration above). Empty
    # when the matching pattern declares no collapse metadata
    # (dashboard_event_signal_collapse_key returns 1 and prints
    # nothing), which means "never collapse, render every occurrence"
    # and is the correct default for every class other than the ones
    # that opt in. Stored in the scratch file as the NO_COLLAPSE_KEY
    # sentinel rather than an empty field: `IFS=$'\t' read` treats runs
    # of tab -- an IFS-whitespace character -- as ONE delimiter and
    # silently drops empty fields between them, so a genuinely empty
    # column here would shift every field after it.
    # The cache-hit test below is `${COLLAPSE_KEY_CACHE[$rest]+set}`, not
    # `-v "COLLAPSE_KEY_CACHE[$rest]"` -- deliberately, and this is load-
    # bearing for the whole cache's O(distinct-identity) cost claim above.
    # `-v` re-lexes its "arrname[subscript]" argument as a second pass of
    # quote/word parsing over the already-expanded $rest text; a $rest
    # value carrying an odd number of literal single-quote characters
    # (any English sentence with a contraction or possessive -- "kanban.
    # cfg's", "doesn't" -- and production's remedy:/message: text is full
    # of these) leaves that inner re-lex with an unterminated quote, which
    # `-v` resolves by silently reporting "not set" rather than erroring.
    # A cache that reports a false miss on every repeat of such a line
    # pays a fresh subshell fork for it every single occurrence instead of
    # once per distinct identity -- exactly the fork-per-occurrence cost
    # the cache exists to avoid, and the dominant real-world cost this
    # ticket's budget fix removes. `${arr[$key]+set}` is plain parameter
    # expansion: $rest is substituted once via normal word expansion with
    # no second quote-parsing pass over its contents, so it reports the
    # cache's true hit/miss state regardless of what punctuation $rest
    # carries.
    if [[ -n "${COLLAPSE_KEY_CACHE[$rest]+set}" ]]; then
      collapse_key="${COLLAPSE_KEY_CACHE[$rest]}"
    else
      # The `_var` accessor, not the printing one: this branch already
      # runs at most once per distinct $rest (the cache above), but a
      # production root's diversity still means dozens to low hundreds
      # of calls per render, and the printing accessor's `$(...)` forks
      # a subshell on every one of them for no reason the caller needs
      # -- writing straight into collapse_key costs no fork at all.
      dashboard_event_signal_collapse_key_var "$line" collapse_key || true
      [[ -n "$collapse_key" ]] || collapse_key="$NO_COLLAPSE_KEY"
      # The capture stays free-form (lib/event_signal_patterns.sh is never
      # narrowed to keep it tab-free), so collapse_key can itself carry a
      # literal tab whenever the source line does -- and this column is
      # NOT the last one in the scratch row, so an embedded tab here would
      # split it into extra `read` fields and shift every column after it
      # (an unescaped embedded tab corrupts the printed text and merges
      # two distinct tab-bearing identities into one, dropping the
      # second). Escape it for storage only: backslash first (so an
      # already-escaped sequence can never be mistaken for one this step
      # produces), then tab -- a bijective mapping, so two distinct raw
      # captures always escape to two distinct strings and identity is
      # preserved exactly. The escaped form is never displayed (this
      # column only ever feeds the count/first-seen identity lookups the
      # closing awk pass below performs), so there is no un-escaping
      # step and no
      # operator-visible text is touched.
      collapse_key="${collapse_key//\\/\\\\}"
      collapse_key="${collapse_key//$'\t'/\\t}"
      COLLAPSE_KEY_CACHE["$rest"]="$collapse_key"
    fi

    # collapse_key sits before rest, not after: rest is free-form line
    # text and belongs last so read's overflow-into-last-field behavior
    # keeps working exactly as it did before this column was added.
    # show_agents (set above; always 0 on this path) sits ahead of it
    # for the same reason collapse_key does -- a fixed, never-free-form
    # column belongs before the free-form one that swallows every
    # field past it.
    #
    # The sort key is date+time PLUS the zero-padded MERGE_SEQ counter,
    # not date+time alone: several rows -- a timestamped parent and
    # every bare continuation line it carries forward its stamp to --
    # legitimately share one HH:MM:SS, and the counter is what gives
    # each of those rows its own distinct key so the sort below has
    # nothing to arbitrate and reproduces this exact write order every
    # time.
    printf '%s#%010d\t%s\t%s\t%s\t%s\t%s\n' "${date_part}T${time_part}" "$MERGE_SEQ" "$agent" "$time_part" "$collapse_key" "$show_agents" "$rest" >&"$MERGE_FD"
    MERGE_SEQ=$(( MERGE_SEQ + 1 ))
  done
done

# Close the held descriptor now that every agent's lines are written --
# the file is complete on disk before anything below reads it.
exec {MERGE_FD}>&-

if [[ ! -s "$MERGE_FILE" ]]; then
  echo "no wake events yet today"
  exit 0
fi

# Sort once into its own scratch file so the fold below can read the
# chronologic stream twice: once to total each collapse identity's
# whole-day occurrence count, once to print. A single-pass pipe can't do
# both, since the first occurrence of a repeated identity must already
# know its EVENTUAL total count -- including occurrences later in the
# stream -- at the moment it is printed.
SORTED_FILE="$(pgai_mktemp event-band-sorted)"
trap 'rm -f "$MERGE_FILE" "$SORTED_FILE"' EXIT
# The MERGE_SEQ suffix folded into field 1 (see the printf above) makes
# every row's sort key distinct, so this sort never has a tie to break:
# rows land in ascending date+time order, and rows that share a
# date+time -- a timestamped parent and the bare continuation lines
# that inherited its stamp -- land in the exact order they were written
# (ascending MERGE_SEQ), which is their original source-file order. -s
# (stable) costs nothing and is kept as a second line of defense should
# a future change ever produce two equal keys.
sort -s -t "$(printf '\t')" -k1,1 "$MERGE_FILE" > "$SORTED_FILE"

# Fold and print in one awk pass over SORTED_FILE rather than two bash
# `while read` passes over it: awk's own field-splitting and array
# lookups run at C speed, and buffering every row into an awk array
# during the single read still lets the END block find each identity's
# EVENTUAL whole-day count -- the same requirement the old two-pass
# split existed to satisfy -- before it prints that identity's first
# occurrence, without re-reading the file a second time to get there.
# A bash `while read` loop over production's tens of thousands of
# surviving rows was measured as a real per-row cost in its own right
# (on top of the ingest loop above); this removes it rather than
# shaving it, the same single-pass shape as the ingest loop above.
#
# Passed in rather than hardcoded so the awk program carries no color
# table, agent roster, or sentinel of its own -- AGENT_COLOR, RESET,
# USE_COLOR, and NO_COLLAPSE_KEY stay declared exactly once, above.
awk -F '\t' \
    -v use_color="$USE_COLOR" \
    -v no_collapse_key="$NO_COLLAPSE_KEY" \
    -v reset="$RESET" \
    -v color_pm="${AGENT_COLOR[pm]:-}" \
    -v color_coder="${AGENT_COLOR[coder]:-}" \
    -v color_writer="${AGENT_COLOR[writer]:-}" \
    -v color_tester="${AGENT_COLOR[tester]:-}" \
    -v color_cm="${AGENT_COLOR[cm]:-}" '
  BEGIN {
    color["pm"] = color_pm
    color["coder"] = color_coder
    color["writer"] = color_writer
    color["tester"] = color_tester
    color["cm"] = color_cm
    n = 0
  }
  {
    # Field 6 (rest) is free-form line text and may itself carry a
    # literal tab -- rebuild it from every field past the fifth,
    # mirroring the bash `read` overflow-into-last-field behavior this
    # row shape was designed around (see the printf that writes it).
    # Field 5 (show_agents) is a fixed "0"/"1" flag, never free-form, so
    # it stays a single field exactly like collapse_key ahead of it.
    agent = $2
    time_part = $3
    collapse_key = $4
    show_agents = $5
    rest = $6
    for (i = 7; i <= NF; i++) {
      rest = rest "\t" $i
    }
    n++
    row_agent[n] = agent
    row_time[n] = time_part
    row_key[n] = collapse_key
    row_show_agents[n] = show_agents
    row_rest[n] = rest
    if (collapse_key != no_collapse_key) {
      count[collapse_key]++
      # Build the distinct-agent list for each collapse identity, in
      # first-seen order (the same order "first seen HH:MM:SS" already
      # reports), regardless of show_agents -- cheap to always track,
      # and only ever read when show_agents=="1" at print time below.
      if (!((collapse_key, agent) in agent_seen)) {
        agent_seen[collapse_key, agent] = 1
        agent_list[collapse_key] = (collapse_key in agent_list) \
          ? agent_list[collapse_key] "," agent \
          : agent
      }
    }
  }
  END {
    # Whole-day totals above are complete now that every row has been
    # read once; this second walk is over the in-memory arrays just
    # built, never a second read of SORTED_FILE from disk.
    for (i = 1; i <= n; i++) {
      agent = row_agent[i]
      time_part = row_time[i]
      collapse_key = row_key[i]
      show_agents = row_show_agents[i]
      rest = row_rest[i]
      if (collapse_key != no_collapse_key) {
        if (collapse_key in printed) {
          continue
        }
        printed[collapse_key] = 1
        if (show_agents == "1") {
          display_text = rest " (x" count[collapse_key] ", first seen " time_part ", agents: " agent_list[collapse_key] ")"
        } else {
          display_text = rest " (x" count[collapse_key] ", first seen " time_part ")"
        }
      } else {
        display_text = rest
      }
      if (use_color == "true") {
        printf "%s[%s %s]%s %s\n", color[agent], agent, time_part, reset, display_text
      } else {
        printf "[%s %s] %s\n", agent, time_part, display_text
      }
    }
  }
' "$SORTED_FILE"
