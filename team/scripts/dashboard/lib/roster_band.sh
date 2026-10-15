#!/usr/bin/env bash
# roster_band.sh — the shared roster band renderer for the two next-fire
# schedule homes: next-cron-firings.sh's pane and show-header.sh's window-0
# tile. status-band.sh reads the entries (not the band) for its NEXT column.
#
# At default geometry the whole roster renders as a compact, full-width
# columnar band across at most PGAI_ROSTER_BAND_MAX_ROWS rows (see
# lib/dashboard_constants.sh), one entry per "AGENT :MM (in M:SS)" /
# "AGENT Day Ham/pm" chunk, joined by PGAI_ROSTER_BAND_SEPARATOR. When the
# hosting pane is too narrow for the whole roster to pack into that many
# rows, the renderer falls back to the pre-existing vertical, one-entry-
# per-line list and applies the pre-existing height-based fit
# (next_fire_pane_height / next_fire_fit_lines from next_fire_viewport.sh)
# — the same "+N more" drop-from-the-bottom narration that list has always
# used, never a second copy of that logic.
#
# Every consumer builds its entry text through the SAME data path —
# roster_entries.py's CLI, invoked via roster_band_fetch_entries below:
# window 0's header (show-header.sh), status-band.sh's NEXT column, and
# next-cron-firings.sh where create.sh still starts it. So they can never
# disagree about which entries exist, their order, or their
# fire-mark/countdown text.
#
# Usage:
#   source ".../lib/next_fire_viewport.sh"   # pane geometry + vertical fit
#   source ".../lib/roster_band.sh"
#   roster_band_fetch_entries <roster_entries_py> <cron_parser_lib> \
#       <crontab_text> <now_ts>
#   roster_band_render <max_width_or_empty> <height_budget_or_empty>

# Source guard: safe to source multiple times.
[[ -n "${_PGAI_ROSTER_BAND_SH_LOADED:-}" ]] && return 0
_PGAI_ROSTER_BAND_SH_LOADED=1

# ---------------------------------------------------------------------------
# roster_band_fetch_entries <roster_entries_py> <cron_parser_lib> \
#     <crontab_text> <now_ts>
#
# Runs roster_entries.py and populates two parallel global arrays in roster
# order (pm first, then crontab first-seen order — never re-sorted):
#   ROSTER_BAND_DISPLAY  — the display text for each entry, e.g.
#                           "PM :00 (in 0:37)" or "OVERWATCH Thu 3am"
#   ROSTER_BAND_AGENTS   — the bare upper-cased agent token for each entry,
#                           e.g. "PM" — callers that need to key a row by
#                           agent read this in lockstep with
#                           ROSTER_BAND_DISPLAY (same index, same entry).
#   ROSTER_BAND_KINDS    — "INT" (sub-hour, fire-mark + countdown) or "STR"
#                           (weekly/infrequent, day+time hint) per entry,
#                           same lockstep indexing.
#
# Both arrays are reset (emptied) on every call, including when the parser
# produces no entries (an empty roster is a valid, non-error result).
# ---------------------------------------------------------------------------
roster_band_fetch_entries() {
  local roster_entries_py="$1" cron_parser_lib="$2" crontab_text="$3" now_ts="$4"

  ROSTER_BAND_DISPLAY=()
  ROSTER_BAND_AGENTS=()
  ROSTER_BAND_KINDS=()

  [[ -z "$roster_entries_py" ]] && return 0
  [[ ! -f "$roster_entries_py" ]] && return 0

  local raw_output=""
  raw_output="$(python3 "$roster_entries_py" "$crontab_text" "$cron_parser_lib" "$now_ts" 2>/dev/null || true)"
  [[ -z "$raw_output" ]] && return 0

  local kind agent rest
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    kind="${line%% *}"
    rest="${line#* }"
    agent="${rest%% *}"
    rest="${rest#* }"
    case "$kind" in
      INT)
        # rest is "<:MM> <M:SS>"
        local mark countdown
        mark="${rest%% *}"
        countdown="${rest#* }"
        ROSTER_BAND_AGENTS+=("$agent")
        ROSTER_BAND_KINDS+=("INT")
        ROSTER_BAND_DISPLAY+=("$(printf '%s %s (in %s)' "$agent" "$mark" "$countdown")")
        ;;
      STR)
        # rest is the label, which may itself contain spaces ("Thu 3am")
        ROSTER_BAND_AGENTS+=("$agent")
        ROSTER_BAND_KINDS+=("STR")
        ROSTER_BAND_DISPLAY+=("$(printf '%s %s' "$agent" "$rest")")
        ;;
      *)
        # Unknown line shape — skip silently (forward compatibility)
        ;;
    esac
  done <<< "$raw_output"
}

# ---------------------------------------------------------------------------
# roster_band_pack <max_width> <sep> <max_rows> <entry> [<entry> ...]
#
# Greedily packs <entry...> into as few rows as possible, each row no
# longer than <max_width> characters, joined by <sep>. Populates
# ROSTER_BAND_PACKED_ROWS (global array, reset on every call) and returns
# 0 when the result fits within <max_rows> rows, 1 when it does not (the
# caller's signal to fall back to the vertical list instead).
#
# <max_width> empty means "unqueryable" (no tmux pane to measure) — treated
# as unconstrained, per next_fire_pane_height's own established contract of
# never truncating to a guessed geometry: every entry packs onto a single
# row and this always returns 0.
#
# A single entry longer than <max_width> on its own still occupies its own
# row rather than looping forever — packing never drops or truncates an
# entry's own text, only decides which row it lands on.
# ---------------------------------------------------------------------------
roster_band_pack() {
  local max_width="$1" sep="$2" max_rows="$3"; shift 3
  local -a entries=("$@")
  ROSTER_BAND_PACKED_ROWS=()

  if [[ -z "$max_width" ]]; then
    local joined="" entry
    for entry in "${entries[@]}"; do
      if [[ -z "$joined" ]]; then
        joined="$entry"
      else
        joined="${joined}${sep}${entry}"
      fi
    done
    [[ -n "$joined" ]] && ROSTER_BAND_PACKED_ROWS+=("$joined")
    return 0
  fi

  local -a rows=()
  local current="" candidate entry
  for entry in "${entries[@]}"; do
    if [[ -z "$current" ]]; then
      candidate="$entry"
    else
      candidate="${current}${sep}${entry}"
    fi
    if [[ -z "$current" ]] || (( ${#candidate} <= max_width )); then
      current="$candidate"
    else
      rows+=("$current")
      current="$entry"
    fi
  done
  [[ -n "$current" ]] && rows+=("$current")

  if (( ${#rows[@]} > max_rows )); then
    return 1
  fi
  ROSTER_BAND_PACKED_ROWS=("${rows[@]}")
  return 0
}

# ---------------------------------------------------------------------------
# roster_band_render <max_width> <max_rows> <sep> <height_budget> <entry...>
#
# The full band-or-vertical decision in one call. Tries roster_band_pack
# first; on success prints ROSTER_BAND_PACKED_ROWS (the columnar band, at
# most <max_rows> lines, no overflow marker — a band that fits never emits
# one). On failure (roster does not fit the width budget), falls back to
# printing one <entry> per line through next_fire_fit_lines (already
# sourced by the caller from next_fire_viewport.sh), the pre-existing
# height-based fit-with-marker — the vertical list, unchanged behavior.
#
# <height_budget> is passed straight through to next_fire_fit_lines; empty
# means unqueryable/unconstrained there too, matching that function's own
# contract.
# ---------------------------------------------------------------------------
roster_band_render() {
  local max_width="$1" max_rows="$2" sep="$3" height_budget="$4"; shift 4
  local -a entries=("$@")

  if roster_band_pack "$max_width" "$sep" "$max_rows" "${entries[@]}"; then
    printf '%s\n' "${ROSTER_BAND_PACKED_ROWS[@]}"
    return 0
  fi

  next_fire_fit_lines "$height_budget" "${entries[@]}"
}
