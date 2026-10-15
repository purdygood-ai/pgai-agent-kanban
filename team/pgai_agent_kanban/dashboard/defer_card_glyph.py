#!/usr/bin/env python3
"""
defer_card_glyph.py — Per-bug deferred-card glyph decision for dashboard column rendering.

The one "which glyph, if any, does this bug card get" decision, used by
both board column panes (pgai_agent_kanban.dashboard.column_render's
single-project and multi-project renderers).

This module makes no resolver or severity decisions itself. The deferral
posture (mode) and the set of currently-deferred bug IDs are resolved by
team/scripts/dashboard/lib/defer_scope.sh (bash), which calls
team/scripts/lib/defer_posture.sh's resolve_defer_posture and team/
scripts/lib/discovery_defer.sh's severity-decision primitives. The mode
words it receives ("off", "on", "malformed") are the ones
pgai_agent_kanban.lib.defer_posture defines (MODE_OFF, MODE_ON,
MODE_MALFORMED; posture_mode maps a resolved posture onto them) -- the one
Python reader of the deferral posture -- and this module reads them from
there. It only turns the already-resolved answer into the glyph suffix a
card renders, and parses the DFS_SEP-joined per-project env-string
defer_scope.sh's bash callers pass across the shell/Python boundary.

Public API
----------
``defer_card_glyph_suffix(mode, cid, status, defer_ids, glyph_deferred_bug,
                           glyph_defer_warning, use_color)``
    Returns the space-plus-glyph suffix (or "") for one bug card, appended
    after the card's id so the id column itself stays left-aligned whether
    or not a given card carries a glyph. See its own docstring for the full
    "malformed marks every open card" contract.

``parse_proj_defer_info(lines, dfs_sep, n_projects)``
    Parses the multi-project "mode<SEP>errfile<SEP>ids-joined-by-pipe" lines
    team/scripts/lib/dashboard/column_render_data_multi.sh's bash stage built (one
    per project, in PROJ_NAMES order) into two n_projects-long parallel
    lists: modes (str) and defer_ids (set[str]). Pads short input with
    ("off", empty set) so index access by project position is always safe.
"""

from __future__ import annotations

from pgai_agent_kanban.lib.defer_posture import (
    MODE_MALFORMED,
    MODE_OFF,
    MODE_ON,
)


def defer_card_glyph_suffix(
    mode: str,
    cid: str,
    status: str,
    defer_ids,
    glyph_deferred_bug: str,
    glyph_defer_warning: str,
    use_color: bool,
) -> str:
    """Rich-mode-only glyph suffix (a single leading space plus glyph, or
    "") for one bug card, meant to be appended after the card's rendered id
    — never before it. Appending rather than prepending is what keeps every
    card's id starting at the same column whether or not that particular
    card carries a glyph; a prefix shifts the id itself and breaks
    alignment across a mixed deferred/undeferred board.

    mode is "off" | "on" | "malformed" (defer_scope.sh's vocabulary).
    "malformed" marks every currently-OPEN bug card with the warning glyph —
    matching discovery's own behavior when resolve_defer_posture refuses
    (nothing gets bundled that tick, so every open bug is held in the same
    limbo). A bug already past "open" (running, done, ...) was never a
    deferral candidate in the first place, malformed posture or not, so it
    renders with no glyph either way. "on" marks a card with the deferred
    glyph only when its compact ID (cid) is a member of defer_ids — the
    exact set defer_scope.sh's dashboard_defer_scan_bugs computed via
    discovery's own severity decision. use_color gates rich-mode-only
    rendering; NO_COLOR/dumb callers pass use_color=False and always get "".
    """
    if not use_color:
        return ""
    if mode == MODE_MALFORMED:
        return f" {glyph_defer_warning}" if (glyph_defer_warning and status == "open") else ""
    if mode == MODE_ON and cid in defer_ids:
        return f" {glyph_deferred_bug}" if glyph_deferred_bug else ""
    return ""


def parse_proj_defer_info(lines, dfs_sep: str, n_projects: int):
    """Parse per-project defer-info lines into (modes, defer_ids) lists.

    Each element of *lines* is "mode<SEP>errfile<SEP>ids-joined-by-pipe"
    (errfile is not returned — callers that need it read the raw line
    themselves; card rendering only ever needs mode and the ID set). Missing
    trailing entries are padded to length *n_projects* with ("off", empty
    set) so `modes[idx]` / `defer_ids[idx]` is always safe for any project
    index in range.
    """
    modes = []
    ids_list = []
    for line in lines:
        parts = line.split(dfs_sep)
        mode = parts[0] if len(parts) > 0 else MODE_OFF
        ids_joined = parts[2] if len(parts) > 2 else ""
        modes.append(mode)
        ids_list.append(set(x for x in ids_joined.split("|") if x))
    while len(modes) < n_projects:
        modes.append(MODE_OFF)
        ids_list.append(set())
    return modes, ids_list
