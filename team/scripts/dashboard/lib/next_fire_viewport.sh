#!/usr/bin/env bash
# next_fire_viewport.sh — shared pane-geometry query and row-fit-with-marker
# helpers for the two next-fire schedule renderers: next-cron-firings.sh's
# window-4 pane and show-header.sh's window-0 cron-firings column (reached
# through show-multi.sh's header-mode single-project delegation).
#
# A roster that grows past the space its pane allots it must never scroll
# rows off the top with no signal anything was cut. These functions query
# the pane actually hosting the output, and when the full roster cannot
# fit, drop rows from the BOTTOM and name the count — never lose a row
# silently. next_fire_pane_height and next_fire_fit_lines are the
# height-based fit the vertical fallback list uses; next_fire_pane_width is
# the horizontal counterpart lib/roster_band.sh uses to decide whether the
# columnar band fits at all before falling back to that vertical list.

# Source guard: safe to source multiple times.
[[ -n "${_PGAI_NEXT_FIRE_VIEWPORT_SH_LOADED:-}" ]] && return 0
_PGAI_NEXT_FIRE_VIEWPORT_SH_LOADED=1

# ---------------------------------------------------------------------------
# next_fire_pane_height
# Echoes the current tmux pane's height in rows, or nothing when it cannot
# be determined: not running inside tmux, the tmux binary is missing, or
# the query itself fails for any reason (a --no-tmux one-shot invocation, a
# piped invocation, or a plain non-tmux terminal all land here). Callers
# treat an empty result as "unknowable" and print every row rather than
# truncate to a guessed height.
# ---------------------------------------------------------------------------
next_fire_pane_height() {
  local h=""
  if [[ -n "${TMUX_PANE:-}" ]] && command -v tmux >/dev/null 2>&1; then
    h="$(tmux display-message -p -t "$TMUX_PANE" '#{pane_height}' 2>/dev/null || true)"
  fi
  if [[ "$h" =~ ^[0-9]+$ ]] && (( h > 0 )); then
    printf '%s\n' "$h"
  fi
}

# ---------------------------------------------------------------------------
# next_fire_pane_width
# Echoes the current tmux pane's width in columns, or nothing when it cannot
# be determined -- same "unknowable" contract as next_fire_pane_height
# above, just for the horizontal dimension. Consumed by the roster band
# renderer (lib/roster_band.sh) to decide whether the full roster packs
# into its columnar band at the current width or must fall back to the
# vertical list; a caller that cannot query width treats the band as
# unconstrained rather than guessing a wrap point.
# ---------------------------------------------------------------------------
next_fire_pane_width() {
  local w=""
  if [[ -n "${TMUX_PANE:-}" ]] && command -v tmux >/dev/null 2>&1; then
    w="$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width}' 2>/dev/null || true)"
  fi
  if [[ "$w" =~ ^[0-9]+$ ]] && (( w > 0 )); then
    printf '%s\n' "$w"
  fi
}

# ---------------------------------------------------------------------------
# next_fire_fit_lines <budget> <line> [<line> ...]
# Prints each <line> unchanged when they all fit within <budget> rows, or
# when <budget> is empty/non-numeric (the "cannot query" case — never
# truncate to a guess). When the line count exceeds a numeric <budget>,
# prints the first (budget - 1) lines followed by a single "+N more"
# marker naming exactly how many rows were dropped — truncation from the
# BOTTOM of the list, never silent.
# ---------------------------------------------------------------------------
next_fire_fit_lines() {
  local budget="$1"; shift
  local -a lines=("$@")
  local total="${#lines[@]}"

  if ! [[ "$budget" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${lines[@]}"
    return 0
  fi

  if (( total <= budget )); then
    printf '%s\n' "${lines[@]}"
    return 0
  fi

  local show=$(( budget - 1 ))
  (( show < 0 )) && show=0
  local dropped=$(( total - show ))

  local i
  for (( i=0; i<show; i++ )); do
    printf '%s\n' "${lines[$i]}"
  done
  printf '+%d more\n' "$dropped"
}
